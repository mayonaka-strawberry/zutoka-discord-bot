"""End-of-turn effect processing.

Ground Rules 5.2.10.2 and Q&A No.102: the turn-end window is its own timing, so
the priority player is re-read from the Chronos medal when the window opens and
resolves their whole batch first. Q&A No.96 adds that a player holding several
turn-end effects chooses the order among their own. Both are driven by
Game._ph_turn_end_effects, which collects the items below and dispatches them;
this module only decides what is eligible and what each item does.

Item kinds, one per distinct turn-end effect a player can hold:
    ITEM_END_DAMAGE  03-027 pending damage (accumulated across copies)
    ITEM_REFLECT     04-100 reflect of the damage reduced this turn
    ITEM_AREA_03_085 03-085 turn-end clock advance
    ITEM_AREA_03_058 03-058 turn-end heal for both players

Self-removal of 03-058/03-085 at 30+ damage taken is NOT handled here: their
text says すぐに, so removal.check_area_removal does it the moment the threshold
is crossed (Q&A No.16).
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
    """A representative instance for the ordering prompt: the set-zone card
    that produced this pending turn-end effect."""
    for instance_id in (player.set_a, player.set_b):
        if instance_id != -1 and EFFECT_T[state.inst_def[instance_id]] == effect_index:
            return instance_id
    return -1


def collect_turn_end_items(state: GameState, player: PlayerState) -> list[tuple[int, int]]:
    """Eligible (kind, instance_id) items for one player, in a stable default
    order. Whether an item still does anything is decided when it executes:
    an earlier item can change HP, the clock, or remove an area enchant."""
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
    """Resolve the whole turn-end window without prompting for order.

    Game._ph_turn_end_effects is the interactive path and is what real play
    uses; this is the non-interactive equivalent for tests and headless callers.
    It follows the same priority-player-first rule (Q&A No.102) and each
    player's default item order.
    """
    priority = state.priority_player
    for player_index in (priority, 1 - priority):
        player = state.players[player_index]
        for kind, _ in collect_turn_end_items(state, player):
            if state.winner != -1:
                return
            execute_turn_end_item(state, player, kind)


def execute_turn_end_item(state: GameState, player: PlayerState, kind: int) -> None:
    """Run one turn-end item.

    Each 03-058 in play heals independently: two players each holding one heal both
    players 10 twice, for +20 each. Q&A No.26 is the duplicate-copies ruling
    (「はい、２枚のカードの効果は重なります」), and 「お互いの」 in the card text says who is
    healed, not how often. (User-confirmed 2026-08-14; the engine previously capped
    the heal at once per window with no source behind it.)
    """
    # Q&A No.96: end conditions stay live during this window, so a removal can fire
    # between two items. No explicit check is needed here — both damaging items go
    # through deal_damage, which runs check_damage_triggered_removal itself, and the
    # remaining items only raise HP or move the clock, neither of which can newly
    # satisfy a "damage taken >= 30" or "HP <= 50" condition.
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

    # Both area items are power-gated (Ground Rules 6.1.3.4) and re-checked here,
    # because an earlier item in this window may have changed the power totals.
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
