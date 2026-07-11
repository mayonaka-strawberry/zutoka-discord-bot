"""
Transcript recording for the regression harnesses.

A transcript is a list of JSON-serializable event dictionaries, serialized as
JSONL with sorted keys and '\n' newlines so files are byte-stable across runs
and platforms. Transcripts must NEVER contain CardInstance.unique_id values or
wall-clock timestamps — both vary between otherwise identical runs.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from zutomayo.models.game_state import GameState


def card_identity(card_or_instance: Any) -> str:
    """Stable pack-id string for a Card or CardInstance, e.g. '02-005'."""
    card = getattr(card_or_instance, 'card', card_or_instance)
    return f'{card.pack:02d}-{card.id:03d}'


def optional_card_identity(card_or_instance: Any) -> Optional[str]:
    if card_or_instance is None:
        return None
    return card_identity(card_or_instance)


class TranscriptRecorder:
    """Collects ordered game events and serializes them to JSONL."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def record_prompt(
        self,
        kind: str,
        player_index: int,
        option_labels: list[str],
        payload_type: str,
        payload: Any,
    ) -> None:
        self.record({
            'event': 'prompt',
            'kind': kind,
            'player_index': player_index,
            'option_labels': option_labels,
            'payload_type': payload_type,
            'payload': payload,
        })

    def record_message(self, target: str, content: Optional[str], embed_title: Optional[str] = None) -> None:
        if content is None and embed_title is None:
            return
        self.record({
            'event': 'message',
            'target': target,
            'content': content,
            'embed_title': embed_title,
        })

    def record_effect_dispatch(self, effect_id: str, player_index: int) -> None:
        self.record({
            'event': 'effect_dispatch',
            'effect_id': effect_id,
            'player_index': player_index,
        })

    def record_render(self, function_name: str, card_identities: list[str]) -> None:
        self.record({
            'event': 'render',
            'function': function_name,
            'cards': card_identities,
        })

    def record_state_digest(self, game_state: 'GameState', label: str) -> None:
        players = []
        for player in game_state.players:
            players.append({
                'hp': player.hp,
                'side': player.side.name,
                'deck': [card_identity(card_instance) for card_instance in player.deck],
                'hand': [card_identity(card_instance) for card_instance in player.hand],
                'power_charger': [card_identity(card_instance) for card_instance in player.power_charger],
                'abyss': [card_identity(card_instance) for card_instance in player.abyss],
                'battle_zone': optional_card_identity(player.battle_zone),
                'set_zone_a': optional_card_identity(player.set_zone_a),
                'set_zone_b': optional_card_identity(player.set_zone_b),
                'set_zone_c': optional_card_identity(player.set_zone_c),
                'total_power': player.total_power,
                'hand_size_bonus': player.hand_size_bonus,
            })
        self.record({
            'event': 'state_digest',
            'label': label,
            'turn': game_state.turn,
            'phase': game_state.current_phase.name,
            'chronos': game_state.chronos,
            'last_battle_winner': game_state.last_battle_winner,
            'result': game_state.result.name,
            'players': players,
        })

    def record_game_result(self, winner: int, turns: int, player_0_final_hp: int, player_1_final_hp: int) -> None:
        self.record({
            'event': 'game_result',
            'winner': winner,
            'turns': turns,
            'player_0_final_hp': player_0_final_hp,
            'player_1_final_hp': player_1_final_hp,
        })

    def to_jsonl(self) -> str:
        lines = [
            json.dumps(event, sort_keys=True, ensure_ascii=False)
            for event in self.events
        ]
        return '\n'.join(lines) + '\n'
