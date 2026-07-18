"""
Game record store for engine_alpha-driven matches (manifest schema version 2).

Reuses the PostgreSQL backend and the event-stream machinery from
``zutomayo.engine.game_persistence`` (the games / game_players /
game_decisions / game_events tables are unchanged). Differences from the
legacy store:

- manifest carries ``schema_version`` 2, ``engine`` / ``engine_format_version``
  markers, and the engine seed that ``Game(seed=...)`` consumes directly,
- decision-log payloads are ``{'action': int, 'timed_out': bool}`` (engine
  decisions) or ``{'card_keys': {...}, 'timed_out': bool}`` (TCG side-deck
  switches), with fingerprints from ``zutomayo.match.decisions``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from zutomayo.engine import game_persistence
from zutomayo.engine.game_persistence import GameRecordStore
from zutomayo.match.decisions import (
    PAYLOAD_ACTION,
    MatchDecisionRequest,
    MatchDecisionResponse,
    purpose_name,
    request_fingerprint,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION_ENGINE_ALPHA = 2
ENGINE_NAME = 'engine_alpha'
ENGINE_FORMAT_VERSION = 1


def card_keys_for_definition_indices(definition_indices: list[int]) -> list[list[int]]:
    """Serialize a deck of engine definition indices as [pack, id] pairs."""
    from engine_alpha.cards import CARD_DB

    keys = []
    for definition_index in definition_indices:
        pack_text, _, number_text = CARD_DB[definition_index].key.partition('-')
        keys.append([int(pack_text), int(number_text)])
    return keys


def definition_indices_for_card_keys(card_key_pairs: list[list[int]]) -> list[int]:
    """Rebuild a deck of engine definition indices from [pack, id] pairs."""
    from engine_alpha.cards import KEY_TO_INDEX

    return [KEY_TO_INDEX[f'{pack:02d}-{card_id:03d}'] for pack, card_id in card_key_pairs]


def build_match_manifest(
    session: Any,
    mode: str,
    engine_seed: int,
    deck_card_keys: dict[int, list[list[int]]],
    extra_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ordered_player_ids = sorted(
        session.player_discord_ids.items(), key=lambda pair: pair[1],
    )
    manifest: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION_ENGINE_ALPHA,
        'engine': ENGINE_NAME,
        'engine_format_version': ENGINE_FORMAT_VERSION,
        'game_id': session.game_id,
        'channel_id': session.channel_id,
        'mode': mode,
        'player_discord_ids': [[discord_id, index] for discord_id, index in ordered_player_ids],
        'player_deck_names': {str(index): name for index, name in session.player_deck_names.items()},
        'is_solo': session.is_solo,
        'solo_difficulty': session.solo_difficulty,
        'is_tcg': session.is_tcg,
        'best_of': session.best_of,
        'random_seed': engine_seed,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    for player_index, keys in deck_card_keys.items():
        manifest[f'deck_{player_index}'] = keys
    if extra_fields:
        manifest.update(extra_fields)
    return manifest


def describe_match_decision(
    request: MatchDecisionRequest, response: MatchDecisionResponse,
) -> dict[str, Any]:
    """Event-stream payload for one decision, readable by the summary view."""
    described: dict[str, Any] = {
        'kind': request.kind,
        'purpose': purpose_name(request.purpose),
        'player_index': request.player_index,
        'prompt_text': request.prompt_text,
        'timed_out': response.timed_out,
    }
    if response.payload_type == PAYLOAD_ACTION:
        action = response.payload
        described['action'] = action
        for option in request.options:
            if option.action == action:
                described['chosen_label'] = option.label
                described['chosen_description'] = option.description
                break
        else:
            engine_request = request.engine_request
            if engine_request is not None and engine_request.is_pass(action):
                described['chosen_label'] = 'PASS'
    else:
        described['card_keys'] = response.payload
    return described


class MatchRecordStore(GameRecordStore):
    """Per-game record handle for schema-version-2 games. Event buffering,
    flushing, and status transitions come from GameRecordStore unchanged."""

    @classmethod
    async def create_for_match(
        cls,
        session: Any,
        mode: str,
        engine_seed: int,
        deck_card_keys: dict[int, list[list[int]]],
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> 'MatchRecordStore':
        manifest = build_match_manifest(session, mode, engine_seed, deck_card_keys, extra_fields)
        await game_persistence.backend.insert_game(manifest)
        return cls(session.game_id, session)

    async def append_decision(
        self,
        request: MatchDecisionRequest,
        response: MatchDecisionResponse,
    ) -> None:
        record = {
            'sequence_number': response.sequence_number,
            'fingerprint': request_fingerprint(request),
            'payload_type': response.payload_type,
            'payload': {
                ('action' if response.payload_type == PAYLOAD_ACTION else 'card_keys'): response.payload,
                'timed_out': response.timed_out,
            },
        }
        await game_persistence.backend.insert_decision(self.game_id, record)

        from zutomayo.engine.game_events import EVENT_DECISION_MADE

        self.emit_event(EVENT_DECISION_MADE, describe_match_decision(request, response))
        await self.flush_events()


async def load_match_decision_log(
    game_id: str,
) -> dict[int, tuple[dict, MatchDecisionResponse]]:
    """Load a schema-version-2 decision log in broker replay format."""
    replay_log: dict[int, tuple[dict, MatchDecisionResponse]] = {}
    for record in await game_persistence.backend.load_decision_records(game_id):
        payload = record['payload']
        value = payload.get('action') if 'action' in payload else payload.get('card_keys')
        response = MatchDecisionResponse(
            sequence_number=record['sequence_number'],
            payload_type=record['payload_type'],
            payload=value,
            timed_out=bool(payload.get('timed_out')),
        )
        replay_log[record['sequence_number']] = (record['fingerprint'], response)
    return replay_log
