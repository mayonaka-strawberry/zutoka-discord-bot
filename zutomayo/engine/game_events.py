"""
Event taxonomy for the permanent per-game event stream (game_events table).

Events are recorded live only (never during replay) and are observation-only:
recording reads game state but never mutates it and never touches the session
RNG, so the deterministic-replay contract and the regression baselines are
unaffected.

The stream drives /zutomayo summary. Requirement coverage:
- every phase of every turn        -> EVENT_PHASE_ENTERED
- every decision by both players   -> EVENT_DECISION_MADE (plus the raw
                                      game_decisions log)
- day/night effect priority        -> EVENT_EFFECT_PRIORITY_DETERMINED
- effect resolution order within a
  player's turn                    -> EVENT_EFFECT_ORDER_CHOSEN and one
                                      EVENT_EFFECT_RESOLVED / SKIPPED_COST
                                      per effect with its order_index

Card references are [pack, id] pairs (the card_keys convention).
"""

from __future__ import annotations

from typing import Any

from zutomayo.engine.decisions import (
    PAYLOAD_CARD_KEYS,
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TEXT,
    PAYLOAD_TIMEOUT,
    DecisionRequest,
    DecisionResponse,
)
from zutomayo.engine.game_persistence import card_keys

EVENT_SERIES_START = 'series_start'
EVENT_MATCH_START = 'match_start'
EVENT_INITIAL_HAND = 'initial_hand'
EVENT_REDRAW = 'redraw'
EVENT_INITIAL_BATTLE_CARD = 'initial_battle_card'
EVENT_PHASE_ENTERED = 'phase_entered'
EVENT_DECISION_MADE = 'decision_made'
EVENT_EFFECT_PRIORITY_DETERMINED = 'effect_priority_determined'
EVENT_EFFECT_ORDER_CHOSEN = 'effect_order_chosen'
EVENT_EFFECT_RESOLVED = 'effect_resolved'
EVENT_EFFECT_SKIPPED_COST = 'effect_skipped_cost'
EVENT_BATTLE_RESULT = 'battle_result'
EVENT_STATE_SNAPSHOT = 'state_snapshot'
EVENT_NARRATION = 'narration'
EVENT_SIDE_DECK_SWAP = 'side_deck_swap'
EVENT_MATCH_RESULT = 'match_result'
EVENT_SERIES_RESULT = 'series_result'
EVENT_GAME_SAVED = 'game_saved'
EVENT_GAME_RESUMED = 'game_resumed'
EVENT_GAME_END = 'game_end'
EVENT_FORFEIT = 'forfeit'


def describe_card_instance(card_instance: Any) -> dict[str, Any]:
    """{'card': [pack, id], 'name': ..., 'effect': ...} for a Card or CardInstance."""
    card = getattr(card_instance, 'card', card_instance)
    return {'card': [card.pack, card.id], 'name': card.name, 'effect': card.effect}


def _zone_card_key(card_instance: Any) -> list[int] | None:
    if card_instance is None:
        return None
    card = getattr(card_instance, 'card', card_instance)
    return [card.pack, card.id]


def build_state_snapshot(game_state: Any) -> dict[str, Any]:
    """Full public-plus-hands snapshot of both players' zones by card identity."""
    players = []
    for player in game_state.players:
        players.append({
            'hp': player.hp,
            'power': player.total_power,
            'hand': card_keys(player.hand),
            'battle_zone': _zone_card_key(player.battle_zone),
            'set_zone_a': _zone_card_key(player.set_zone_a),
            'set_zone_b': _zone_card_key(player.set_zone_b),
            'set_zone_c': _zone_card_key(player.set_zone_c),
            'power_charger': card_keys(player.power_charger),
            'abyss': card_keys(player.abyss),
            'deck_count': len(player.deck),
        })
    return {
        'turn': game_state.turn,
        'chronos': game_state.chronos,
        'day_night': game_state.day_night.name,
        'players': players,
    }


def describe_decision(request: DecisionRequest, response: DecisionResponse) -> dict[str, Any]:
    """Human-readable mirror of one logged decision, for the summary renderer."""
    if response.payload_type == PAYLOAD_INDICES and response.payload:
        chosen = [
            f'{request.options[index].label} {request.options[index].description}'.strip()
            for index in response.payload
            if 0 <= index < len(request.options)
        ]
    elif response.payload_type == PAYLOAD_NUMBER:
        chosen = [str(response.payload)]
    elif response.payload_type == PAYLOAD_TEXT:
        chosen = [str(response.payload)]
    elif response.payload_type == PAYLOAD_CARD_KEYS:
        chosen = [response.payload]
    elif response.payload_type == PAYLOAD_TIMEOUT:
        chosen = []
    else:
        chosen = []
    return {
        'sequence_number': response.sequence_number,
        'player_index': request.player_index,
        'kind': request.kind,
        'purpose': request.purpose,
        'prompt_text': request.prompt_text,
        'chosen': chosen,
        'payload_type': response.payload_type,
    }
