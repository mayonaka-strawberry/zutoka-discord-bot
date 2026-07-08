"""
Per-game persistence for restart resumability.

Each in-flight match owns a directory under zutomayo/active_games/<game_id>/:

- manifest.json — written once when the match is initialized (after decks are
  chosen): session identity, mode, player ids, the RNG seed, and the exact
  pre-shuffle deck lists. Everything else about the game is reproducible from
  the seed plus the decision log.
- decisions.jsonl — append-only, one line per DecisionResponse with the
  request fingerprint, written through the broker. A TCG series uses one log
  for the whole series (matches and switch phases replay in order).

On startup the resume manager replays each directory: the game coroutine is
re-run from move zero with logged decisions fed back instantly and the
transport muted; when the log is exhausted the game goes live again. The
directory is deleted whenever the session is removed from the session manager
(game end, forfeit, or error).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from zutomayo.engine.decisions import DecisionRequest, DecisionResponse, request_fingerprint

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession

log = logging.getLogger(__name__)

ACTIVE_GAMES_DIRECTORY = Path(__file__).resolve().parent.parent / 'active_games'

MANIFEST_FILE_NAME = 'manifest.json'
DECISIONS_FILE_NAME = 'decisions.jsonl'
SCHEMA_VERSION = 1


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


class GamePersistence:
    def __init__(self, game_directory: Path) -> None:
        self.game_directory = game_directory
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Creation and attachment
    # ------------------------------------------------------------------

    @classmethod
    def create_for_session(
        cls,
        session: 'GameSession',
        mode: str,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> 'GamePersistence':
        """
        Write the manifest for a freshly initialized match and return the
        persistence handle. Deck lists are taken from the game state, which at
        initialization time holds the pre-shuffle order; shuffles draw from
        the session's seeded generator, so replay regenerates them.
        """
        game_directory = ACTIVE_GAMES_DIRECTORY / session.game_id
        game_directory.mkdir(parents=True, exist_ok=True)

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

        persistence = cls(game_directory)
        persistence._write_manifest(manifest)
        return persistence

    @classmethod
    def attach_for_resume(cls, game_directory: Path) -> 'GamePersistence':
        """Attach to an existing directory; new decisions append to the same log."""
        return cls(game_directory)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        final_path = self.game_directory / MANIFEST_FILE_NAME
        temporary_path = final_path.with_suffix('.json.tmp')
        with open(temporary_path, 'w', encoding='utf-8') as manifest_file:
            json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.replace(temporary_path, final_path)

    # ------------------------------------------------------------------
    # Decision log
    # ------------------------------------------------------------------

    async def append_decision(self, request: DecisionRequest, response: DecisionResponse) -> None:
        record = {
            'sequence_number': response.sequence_number,
            'fingerprint': request_fingerprint(request),
            'payload_type': response.payload_type,
            'payload': response.payload,
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        async with self._write_lock:
            await asyncio.to_thread(self._append_line, line)

    def _append_line(self, line: str) -> None:
        decisions_path = self.game_directory / DECISIONS_FILE_NAME
        with open(decisions_path, 'a', encoding='utf-8', newline='\n') as decisions_file:
            decisions_file.write(line + '\n')
            decisions_file.flush()
            os.fsync(decisions_file.fileno())

    def delete(self) -> None:
        shutil.rmtree(self.game_directory, ignore_errors=True)


# ----------------------------------------------------------------------
# Loading (resume path)
# ----------------------------------------------------------------------


def list_game_directories() -> list[Path]:
    if not ACTIVE_GAMES_DIRECTORY.exists():
        return []
    return sorted(path for path in ACTIVE_GAMES_DIRECTORY.iterdir() if path.is_dir())


def load_manifest(game_directory: Path) -> dict[str, Any]:
    with open(game_directory / MANIFEST_FILE_NAME, 'r', encoding='utf-8') as manifest_file:
        return json.load(manifest_file)


def load_decision_log(game_directory: Path) -> dict[int, tuple[dict, DecisionResponse]]:
    """
    Load the decision log in broker replay format. A torn final line (crash
    mid-append) is dropped with a warning; everything before it is intact
    because appends are fsync'd line by line.
    """
    decisions_path = game_directory / DECISIONS_FILE_NAME
    replay_log: dict[int, tuple[dict, DecisionResponse]] = {}
    if not decisions_path.exists():
        return replay_log
    with open(decisions_path, 'r', encoding='utf-8') as decisions_file:
        for line_number, line in enumerate(decisions_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.warning(
                    'Dropping torn decision-log line %d in %s', line_number, game_directory,
                )
                break
            response = DecisionResponse(
                sequence_number=record['sequence_number'],
                payload_type=record['payload_type'],
                payload=record['payload'],
            )
            replay_log[record['sequence_number']] = (record['fingerprint'], response)
    return replay_log
