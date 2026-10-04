"""Abyss and power-charger placement. Route every placement through here so the turn flags fire:

- PF_ABYSS_RECEIVED: set on the abyss owner, whoever placed the card (04-030).
- PF_OPP_CARD_TO_ABYSS: set on the placer's opponent (03-055, 03-091).
- PF_CARD_TO_POWER / PF_CHAR_TO_POWER: set only when owners place into their own
  charger (04-033, 02-058). 04-006's forced placement does not count.

These append to the destination; the caller removes the card from its source.
"""

from __future__ import annotations

from .cards import SEND_TO_POWER_T, CARD_TYPE_T, TYPE_CHARACTER
from .events import EVENT_DRAW, EVENT_PLACED_IN_ABYSS, EVENT_PLACED_IN_CHARGER
from .state import GameState, PF_ABYSS_RECEIVED, PF_OPP_CARD_TO_ABYSS, PF_CARD_TO_POWER, PF_CHAR_TO_POWER


def place_in_abyss(state: GameState, instance_id: int, owner_index: int, actor_index: int) -> None:
    state.inst_attr_ovr[instance_id] = -1
    state.inst_neg[instance_id] = 0
    state.inst_face_up[instance_id] = 1
    state.players[owner_index].abyss.append(instance_id)
    state.players[owner_index].flags[PF_ABYSS_RECEIVED] = 1
    state.players[1 - actor_index].flags[PF_OPP_CARD_TO_ABYSS] = 1
    if state.event_sink is not None:
        state.event_sink.append(
            (EVENT_PLACED_IN_ABYSS, owner_index, actor_index, state.inst_def[instance_id]))


def place_in_charger(state: GameState, instance_id: int, owner_index: int, actor_index: int) -> None:
    state.inst_attr_ovr[instance_id] = -1
    state.inst_neg[instance_id] = 0
    state.inst_face_up[instance_id] = 1
    state.players[owner_index].charger.append(instance_id)
    if actor_index == owner_index:
        flags = state.players[owner_index].flags
        flags[PF_CARD_TO_POWER] = 1
        if CARD_TYPE_T[state.inst_def[instance_id]] == TYPE_CHARACTER:
            flags[PF_CHAR_TO_POWER] = 1
    if state.event_sink is not None:
        state.event_sink.append(
            (EVENT_PLACED_IN_CHARGER, owner_index, actor_index, state.inst_def[instance_id]))


def to_power_or_abyss(state: GameState, instance_id: int, owner_index: int,
                      actor_index: int | None = None) -> None:
    """Route a card leaving play: charger if it has SEND TO POWER, else abyss."""
    actor = owner_index if actor_index is None else actor_index
    if SEND_TO_POWER_T[state.inst_def[instance_id]] > 0:
        place_in_charger(state, instance_id, owner_index, actor)
    else:
        place_in_abyss(state, instance_id, owner_index, actor)


def draw_cards(state: GameState, player_index: int, count: int) -> int:
    """Draw up to `count` cards; returns how many. Drawn cards turn face down and lose effect negation."""
    player = state.players[player_index]
    drawn = player.deck[:count]
    del player.deck[:count]
    for instance_id in drawn:
        state.inst_face_up[instance_id] = 0
        state.inst_neg[instance_id] = 0
    player.hand.extend(drawn)
    if state.event_sink is not None and drawn:
        state.event_sink.append((EVENT_DRAW, player_index, len(drawn)))
    return len(drawn)
