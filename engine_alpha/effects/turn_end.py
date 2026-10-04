"""Turn-end effects: what is eligible and what each item does.
Game._ph_turn_end_effects runs the window: priority player first, and each
player orders their own items.

Items:
    ITEM_END_DAMAGE  03-027 pending damage (summed across copies)
    ITEM_REFLECT     04-100 reflects the damage reduced this turn
    ITEM_AREA_03_085 03-085 clock advance
    ITEM_AREA_03_058 03-058 heal for both players

03-058 and 03-085 leave play at 30+ damage immediately, in removal.py (Q&A No.16).
"""

from __future__ import annotations

from ..cards import EFFECT_T, EFFECT_TO_INDEX
from ..events import EVENT_HP_CHANGED
from ..state import (
    GameState, PlayerState,
    PF_END_OF_TURN_DAMAGE, PF_REFLECT_REDUCTION, PF_DAMAGE_REDUCED,
)

FX_03_058 = EFFECT_TO_INDEX["03-058"]
FX_03_085 = EFFECT_TO_INDEX["03-085"]
FX_03_027 = EFFECT_TO_INDEX["03-027"]
FX_04_100 = EFFECT_TO_INDEX["04-100"]

CHRONOS_SIZE = 18

ITEM_END_DAMAGE = 0
ITEM_REFLECT = 1
ITEM_AREA_03_085 = 2
ITEM_AREA_03_058 = 3


def _find_set_instance(state: GameState, player: PlayerState, effect_index: int) -> int:
    """The set-zone card behind a turn-end item, shown in the ordering prompt."""
    for instance_id in (player.set_a, player.set_b):
        if instance_id != -1 and EFFECT_T[state.inst_def[instance_id]] == effect_index:
            return instance_id
    return -1


def collect_turn_end_items(state: GameState, player: PlayerState) -> list[tuple[int, int]]:
    """A player's eligible (kind, instance_id) items in default order. Each item
    re-checks itself when it runs."""
    items: list[tuple[int, int]] = []
    if player.flags[PF_END_OF_TURN_DAMAGE] > 0:
        items.append((ITEM_END_DAMAGE, _find_set_instance(state, player, FX_03_027)))
    if player.flags[PF_REFLECT_REDUCTION] and player.flags[PF_DAMAGE_REDUCED] > 0:
        items.append((ITEM_REFLECT, _find_set_instance(state, player, FX_04_100)))
    area = player.set_c
    if area != -1:
        effect = EFFECT_T[state.inst_def[area]]
        if effect == FX_03_085:
            items.append((ITEM_AREA_03_085, area))
        elif effect == FX_03_058:
            items.append((ITEM_AREA_03_058, area))
    return items


def process_end_of_turn_effects(state: GameState) -> None:
    """Resolve the turn-end window without ordering prompts, for tests and headless
    callers: priority player first (Q&A No.102), default item order."""
    priority = state.priority_player
    for player_index in (priority, 1 - priority):
        player = state.players[player_index]
        for kind, _ in collect_turn_end_items(state, player):
            if state.winner != -1:
                return
            execute_turn_end_item(state, player, kind)


def execute_turn_end_item(state: GameState, player: PlayerState, kind: int) -> None:
    """Run one turn-end item. Each copy of 03-058 heals separately (Q&A No.26)."""
    # Removals can fire between items (Q&A No.96); deal_damage handles that for the
    # two damaging items.
    from ..battle import (
        deal_damage, effective_power_cost, set_chronos, total_power,
    )

    if kind == ITEM_END_DAMAGE:
        # The flag sits on 03-027's OWNER; the damage lands on their opponent.
        deal_damage(state, 1 - player.index, player.flags[PF_END_OF_TURN_DAMAGE])
        return

    if kind == ITEM_REFLECT:
        deal_damage(state, 1 - player.index, player.flags[PF_DAMAGE_REDUCED])
        return

    # Area items are power-gated (Ground Rules 6.1.3.4); re-checked because an earlier
    # item can change power.
    area = player.set_c
    if area == -1:
        return
    if total_power(state, player) < effective_power_cost(state, area):
        return

    if kind == ITEM_AREA_03_085:
        if not state.is_night:
            set_chronos(state, (state.chronos + 2) % CHRONOS_SIZE)
        return

    if kind == ITEM_AREA_03_058:
        for heal_index in (0, 1):
            heal_player = state.players[heal_index]
            old_hp = heal_player.hp
            heal_player.hp = min(100, heal_player.hp + 10)
            if state.event_sink is not None and heal_player.hp != old_hp:
                state.event_sink.append(
                    (EVENT_HP_CHANGED, heal_index, heal_player.hp - old_hp,
                     heal_player.hp))
        return
