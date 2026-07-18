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
from typing import TYPE_CHECKING, Any, Optional

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

    async def insert_events(self, game_id: str, events: list[dict[str, Any]]) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            await connection.executemany(
                '''
                INSERT INTO game_events (game_id, event_index, match_number, turn, phase, event_type, payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (game_id, event_index) DO NOTHING
                ''',
                [
                    (
                        game_id, event['event_index'], event['match_number'],
                        event['turn'], event['phase'], event['event_type'], event['payload'],
                    )
                    for event in events
                ],
            )

    async def next_event_index(self, game_id: str) -> int:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            return await connection.fetchval(
                'SELECT COALESCE(MAX(event_index), -1) + 1 FROM game_events WHERE game_id = $1',
                game_id,
            )

    async def load_events(self, game_id: str) -> list[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                '''
                SELECT event_index, match_number, turn, phase, event_type, payload
                FROM game_events WHERE game_id = $1 ORDER BY event_index
                ''',
                game_id,
            )
        return [dict(row) for row in rows]

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

    async def list_saved_games_for_player(
        self, discord_id: int, prefix: str = '', limit: int = 25,
    ) -> list[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                '''
                SELECT games.game_id, games.mode, games.is_tcg, games.best_of, games.saved_at
                FROM games
                JOIN game_players ON game_players.game_id = games.game_id
                WHERE game_players.discord_id = $1
                  AND games.status = 'saved'
                  AND games.game_id LIKE $2 || '%'
                ORDER BY games.saved_at DESC
                LIMIT $3
                ''',
                discord_id, prefix, limit,
            )
        return [dict(row) for row in rows]

    async def search_finished_games(self, prefix: str = '', limit: int = 25) -> list[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                '''
                SELECT game_id, mode, is_tcg, best_of, status, created_at, winner_index
                FROM games
                WHERE status IN ('completed', 'quit', 'abandoned')
                  AND game_id LIKE $1 || '%'
                ORDER BY created_at DESC
                LIMIT $2
                ''',
                prefix, limit,
            )
        return [dict(row) for row in rows]

    async def list_recent_games_for_player(self, discord_id: int, limit: int = 15) -> list[dict[str, Any]]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                '''
                SELECT games.game_id, games.mode, games.is_tcg, games.best_of,
                       games.status, games.created_at, games.winner_index,
                       own_seat.player_index,
                       opponent_seat.discord_id AS opponent_discord_id
                FROM games
                JOIN game_players AS own_seat
                  ON own_seat.game_id = games.game_id AND own_seat.discord_id = $1
                LEFT JOIN game_players AS opponent_seat
                  ON opponent_seat.game_id = games.game_id
                 AND opponent_seat.player_index = 1 - own_seat.player_index
                WHERE games.status IN ('completed', 'quit', 'abandoned')
                ORDER BY games.created_at DESC
                LIMIT $2
                ''',
                discord_id, limit,
            )
        return [dict(row) for row in rows]


backend = PostgresGameRecordBackend()


# ----------------------------------------------------------------------
# Per-game handle
# ----------------------------------------------------------------------


class GameRecordStore:
    def __init__(self, game_id: str, session: Optional['GameSession'] = None) -> None:
        self.game_id = game_id
        # Used only to suppress event recording during replay (the replayed
        # portion was already recorded live before the crash or save).
        self.session = session

        # Event stream state. emit_event is a synchronous enqueue; the buffer
        # drains at every decision append and status transition, so a game
        # always flushes at its end and at most the events since the last
        # decision can be lost in a hard crash.
        self.event_buffer: list[dict[str, Any]] = []
        self.next_event_index = 0
        self.current_match_number: Optional[int] = 1
        self.current_turn: Optional[int] = None
        self.current_phase: Optional[str] = None

    @classmethod
    def attach_for_resume(cls, game_id: str, session: Optional['GameSession'] = None) -> 'GameRecordStore':
        """Attach to an existing game record; new decisions append to the same
        log. The caller must seed next_event_index (see next_event_index())
        so event numbering continues where the record left off."""
        return cls(game_id, session)

    def _replaying(self) -> bool:
        return (
            self.session is not None
            and self.session.broker is not None
            and self.session.broker.replaying
        )

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        match_number: Optional[int] = None,
        turn: Optional[int] = None,
        phase: Optional[str] = None,
    ) -> None:
        """
        Enqueue one event. Observation-only and synchronous: never reads the
        session RNG, never mutates game state, and is suppressed during
        replay. Context columns default to the last seen values so mid-phase
        emitters (the effect engine) need not thread them through.
        """
        if self._replaying():
            return
        if match_number is not None:
            self.current_match_number = match_number
        if turn is not None:
            self.current_turn = turn
        if phase is not None:
            self.current_phase = phase
        self.event_buffer.append({
            'event_index': self.next_event_index,
            'match_number': self.current_match_number,
            'turn': self.current_turn,
            'phase': self.current_phase,
            'event_type': event_type,
            'payload': payload,
        })
        self.next_event_index += 1

    async def flush_events(self) -> None:
        if not self.event_buffer:
            return
        pending, self.event_buffer = self.event_buffer, []
        try:
            await backend.insert_events(self.game_id, pending)
        except Exception:
            # Keep the events queued for the next flush point.
            self.event_buffer = pending + self.event_buffer
            log.exception('Failed to flush %d event(s) for game %s', len(pending), self.game_id)

    async def set_status(
        self,
        status: str,
        *,
        winner_index: Optional[int] = None,
        result_summary: Optional[dict] = None,
        channel_id: Optional[int] = None,
    ) -> None:
        await self.flush_events()
        await backend.update_status(
            self.game_id, status,
            winner_index=winner_index, result_summary=result_summary, channel_id=channel_id,
        )


# ----------------------------------------------------------------------
# Loading (resume path)
# ----------------------------------------------------------------------


async def load_manifest(game_id: str) -> Optional[dict[str, Any]]:
    return await backend.load_manifest(game_id)


async def list_game_ids_with_status(status: str) -> list[str]:
    return await backend.list_game_ids_with_status(status)


async def next_event_index(game_id: str) -> int:
    return await backend.next_event_index(game_id)


async def get_game_row(game_id: str) -> Optional[dict[str, Any]]:
    return await backend.get_game_row(game_id)


async def list_saved_games_for_player(
    discord_id: int, prefix: str = '', limit: int = 25,
) -> list[dict[str, Any]]:
    return await backend.list_saved_games_for_player(discord_id, prefix, limit)


async def search_finished_games(prefix: str = '', limit: int = 25) -> list[dict[str, Any]]:
    return await backend.search_finished_games(prefix, limit)


async def list_recent_games_for_player(discord_id: int, limit: int = 15) -> list[dict[str, Any]]:
    return await backend.list_recent_games_for_player(discord_id, limit)


async def load_events(game_id: str) -> list[dict[str, Any]]:
    return await backend.load_events(game_id)
