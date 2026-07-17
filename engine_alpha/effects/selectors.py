"""Selector evaluation: Sel -> ordered list of instance ids."""

from __future__ import annotations

from ..cards import CARD_TYPE_T, ATTRIBUTE_T, SONG_T, SEND_TO_POWER_T, POWER_COST_T
from ..state import GameState
from .ir import Sel


def effective_attribute(state: GameState, instance_id: int) -> int:
    override = state.inst_attr_ovr[instance_id]
    return override if override != -1 else ATTRIBUTE_T[state.inst_def[instance_id]]


def eval_selector(state: GameState, owner_index: int, sel: Sel) -> list[int]:
    player = state.players[owner_index if sel.side == 0 else 1 - owner_index]
    if sel.zone == "hand":
        pool = list(player.hand)
    elif sel.zone == "charger":
        pool = list(player.charger)
    elif sel.zone == "abyss":
        pool = list(player.abyss)
    elif sel.zone == "deck":
        pool = list(player.deck)
    elif sel.zone == "battle":
        pool = [player.battle] if player.battle != -1 else []
    elif sel.zone == "set_c":
        pool = [player.set_c] if player.set_c != -1 else []
    else:
        raise ValueError(f"unknown selector zone {sel.zone!r}")

    if sel.top_n:
        pool = pool[:sel.top_n]

    result = []
    for instance_id in pool:
        def_index = state.inst_def[instance_id]
        if sel.card_type != -1 and CARD_TYPE_T[def_index] != sel.card_type:
            continue
        if sel.attribute != -1 and effective_attribute(state, instance_id) != sel.attribute:
            continue
        if sel.song != -1 and SONG_T[def_index] != sel.song:
            continue
        if sel.stp_ge != -1 and SEND_TO_POWER_T[def_index] < sel.stp_ge:
            continue
        if sel.stp_eq != -1 and SEND_TO_POWER_T[def_index] != sel.stp_eq:
            continue
        if sel.cost_ge != -1 and POWER_COST_T[def_index] < sel.cost_ge:
            continue
        result.append(instance_id)
    return result
