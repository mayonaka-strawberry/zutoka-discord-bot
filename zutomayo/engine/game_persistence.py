"""
Per-game record store: permanent game records, decision logs, and status.

Every game owns a row in the PostgreSQL games table (created once decks are
final), player rows in game_players, and an append-only decision log in
game_decisions written through the broker. The manifest JSONB column keeps
the exact shape the file-based store used: session identity, mode, player
ids, the RNG seed, and the pre-shuffle deck lists. Everything else about a
game is reproducible from the seed plus the decision log.

Records are permanent. Game lifecycle is tracked by games.status
('active', 'saved', 'completed', 'quit', 'abandoned', 'divergence_failed');
nothing is deleted when a game ends. On startup the resume manager replays
every 'active' game: the game coroutine is re-run from move zero with logged
decisions fed back instantly and the transport muted; when the log is
exhausted the game goes live again.

Storage access goes through the module-level `backend` attribute
(PostgresGameRecordBackend in production); tests swap in an in-memory fake.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from zutomayo.engine.decisions import DecisionRequest, DecisionResponse, request_fingerprint

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

STATUS_ACTIVE = 'active'
STATUS_SAVED = 'saved'
STATUS_COMPLETED = 'completed'
STATUS_QUIT = 'quit'
STATUS_ABANDONED = 'abandoned'
STATUS_DIVERGENCE_FAILED = 'divergence_failed'

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_QUIT, STATUS_ABANDONED, STATUS_DIVERGENCE_FAILED)
SUMMARY_ELIGIBLE_STATUSES = (STATUS_COMPLETED, STATUS_QUIT, STATUS_ABANDONED)


def card_keys(cards: list[Any]) -> list[list[int]]:
    """Serialize Card or CardInstance lists as [pack, id] pairs."""
    keys = []
    for card_or_instance in cards:
        card = getattr(card_or_instance, 'card', card_or_instance)
        keys.append([card.pack, card.id])
    return keys


def resolve_card_keys(card_keys: list[list[int]], card_index: dict) -> list[Any]:
    """Rebuild Card lists from [pack, id] pairs using the card index."""
    return [card_index[(pack, card_id)] for pack, card_id in card_keys]


def build_manifest(
    session: 'GameSession',
    mode: str,
    extra_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Manifest for a freshly initialized match. Deck lists are taken from the
    game state, which at initialization time holds the pre-shuffle order;
    shuffles draw from the session's seeded generator, so replay regenerates
    them.
    """
    ordered_player_ids = sorted(
        session.player_discord_ids.items(), key=lambda pair: pair[1],
    )
    manifest: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'game_id': session.game_id,
        'channel_id': session.channel_id,
        'mode': mode,
        'player_discord_ids': [[discord_id, index] for discord_id, index in ordered_player_ids],
        'player_deck_names': {str(index): name for index, name in session.player_deck_names.items()},
        'is_solo': session.is_solo,
        'solo_difficulty': session.solo_difficulty,
        'is_tcg': session.is_tcg,
        'best_of': session.best_of,
        'random_seed': session.random_seed,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    if session.game_state is not None:
        for index in range(2):
            manifest[f'deck_{index}'] = card_keys(session.game_state.players[index].deck)
    if extra_fields:
        manifest.update(extra_fields)
    return manifest


# ----------------------------------------------------------------------
# PostgreSQL backend
# ----------------------------------------------------------------------


class PostgresGameRecordBackend:
    async def insert_game(self, manifest: dict[str, Any]) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    '''
                    INSERT INTO games (
                        game_id, schema_version, status, mode, channel_id,
                        is_solo, solo_difficulty, is_tcg, best_of,
                        random_seed, manifest
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (game_id) DO NOTHING
                    ''',
                    manifest['game_id'], manifest['schema_version'], STATUS_ACTIVE,
                    manifest['mode'], manifest['channel_id'],
                    manifest['is_solo'], manifest['solo_difficulty'],
                    manifest['is_tcg'], manifest['best_of'],
                    manifest['random_seed'], manifest,
                )
                deck_names = manifest.get('player_deck_names', {})
                for discord_id, player_index in manifest['player_discord_ids']:
                    await connection.execute(
                        '''
                        INSERT INTO game_players (game_id, player_index, discord_id, deck_name)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (game_id, player_index) DO NOTHING
                        ''',
                        manifest['game_id'], player_index, discord_id,
                        deck_names.get(str(player_index)),
                    )

    async def insert_decision(self, game_id: str, record: dict[str, Any]) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            await connection.execute(
                '''
                INSERT INTO game_decisions (game_id, sequence_number, fingerprint, payload_type, payload)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (game_id, sequence_number) DO NOTHING
                ''',
                game_id, record['sequence_number'], record['fingerprint'],
                record['payload_type'], record['payload'],
            )

    async def update_status(
        self,
        game_id: str,
        status: str,
        *,
        winner_index: Optional[int] = None,
        result_summary: Optional[dict] = None,
        channel_id: Optional[int] = None,
    ) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            await connection.execute(
                '''
                UPDATE games SET
                    status = $2,
                    winner_index = COALESCE($3, winner_index),
                    result_summary = COALESCE($4, result_summary),
                    channel_id = COALESCE($5, channel_id),
                    saved_at = CASE WHEN $2 = 'saved' THEN now() ELSE saved_at END,
                    ended_at = CASE WHEN $2 IN ('completed', 'quit', 'abandoned', 'divergence_failed')
                               THEN now() ELSE ended_at END
                WHERE game_id = $1
                ''',
                game_id, status, winner_index, result_summary, channel_id,
            )

    async def load_manifest(self, game_id: str) -> Optional[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            manifest = await connection.fetchval(
                'SELECT manifest FROM games WHERE game_id = $1', game_id,
            )
        return manifest

    async def load_decision_records(self, game_id: str) -> list[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                '''
                SELECT sequence_number, fingerprint, payload_type, payload
                FROM game_decisions WHERE game_id = $1 ORDER BY sequence_number
                ''',
                game_id,
            )
        return [
            {
                'sequence_number': row['sequence_number'],
                'fingerprint': row['fingerprint'],
                'payload_type': row['payload_type'],
                'payload': row['payload'],
            }
            for row in rows
        ]

    async def list_game_ids_with_status(self, status: str) -> list[str]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                'SELECT game_id FROM games WHERE status = $1 ORDER BY created_at', status,
            )
        return [row['game_id'] for row in rows]

    async def get_game_row(self, game_id: str) -> Optional[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            row = await connection.fetchrow('SELECT * FROM games WHERE game_id = $1', game_id)
        return dict(row) if row is not None else None


backend = PostgresGameRecordBackend()


# ----------------------------------------------------------------------
# Per-game handle
# ----------------------------------------------------------------------


class GameRecordStore:
    def __init__(self, game_id: str) -> None:
        self.game_id = game_id

    @classmethod
    async def create_for_session(
        cls,
        session: 'GameSession',
        mode: str,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> 'GameRecordStore':
        """Insert the game record for a freshly initialized match and return the handle."""
        manifest = build_manifest(session, mode, extra_fields)
        await backend.insert_game(manifest)
        return cls(session.game_id)

    @classmethod
    def attach_for_resume(cls, game_id: str) -> 'GameRecordStore':
        """Attach to an existing game record; new decisions append to the same log."""
        return cls(game_id)

    async def append_decision(self, request: DecisionRequest, response: DecisionResponse) -> None:
        record = {
            'sequence_number': response.sequence_number,
            'fingerprint': request_fingerprint(request),
            'payload_type': response.payload_type,
            'payload': response.payload,
        }
        await backend.insert_decision(self.game_id, record)

    async def set_status(
        self,
        status: str,
        *,
        winner_index: Optional[int] = None,
        result_summary: Optional[dict] = None,
        channel_id: Optional[int] = None,
    ) -> None:
        await backend.update_status(
            self.game_id, status,
            winner_index=winner_index, result_summary=result_summary, channel_id=channel_id,
        )


# ----------------------------------------------------------------------
# Loading (resume path)
# ----------------------------------------------------------------------


async def load_manifest(game_id: str) -> Optional[dict[str, Any]]:
    return await backend.load_manifest(game_id)


async def load_decision_log(game_id: str) -> dict[int, tuple[dict, DecisionResponse]]:
    """Load the decision log in broker replay format."""
    replay_log: dict[int, tuple[dict, DecisionResponse]] = {}
    for record in await backend.load_decision_records(game_id):
        response = DecisionResponse(
            sequence_number=record['sequence_number'],
            payload_type=record['payload_type'],
            payload=record['payload'],
        )
        replay_log[record['sequence_number']] = (record['fingerprint'], response)
    return replay_log


async def list_game_ids_with_status(status: str) -> list[str]:
    return await backend.list_game_ids_with_status(status)
