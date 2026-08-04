"""End-of-turn effect processing (old process_end_of_turn_effects).

Order matters and matches the old engine exactly:
1. 03-027 pending end-of-turn damage (counts toward the >=30 threshold)
2. 04-100 reflect: reduced battle damage is dealt to the opponent
3. 03-085: self-remove at >=30 total damage taken, else +2 clock if daytime
4. 03-058: self-remove at >=30 total damage taken, else heal both 10 (once)
"""

from __future__ import annotations

from ..cards import EFFECT_T, EFFECT_TO_INDEX
from ..events import EVENT_HP_CHANGED
from ..state import GameState, PF_END_OF_TURN_DAMAGE, PF_REFLECT_REDUCTION, PF_DAMAGE_REDUCED, PF_DAMAGE_TAKEN
from ..zones import place_in_abyss

FX_03_058 = EFFECT_TO_INDEX["03-058"]
FX_03_085 = EFFECT_TO_INDEX["03-085"]

CHRONOS_SIZE = 18


def process_end_of_turn_effects(state: GameState) -> None:
    from ..battle import deal_damage, effective_power_cost, set_chronos, total_power

    for player_index in (0, 1):
        deal_damage(state, player_index, state.players[player_index].flags[PF_END_OF_TURN_DAMAGE])

    for player_index in (0, 1):
        if state.players[player_index].flags[PF_REFLECT_REDUCTION]:
            deal_damage(state, 1 - player_index,
                        state.players[player_index].flags[PF_DAMAGE_REDUCED])

    for player in state.players:
        area = player.set_c
        if area == -1 or EFFECT_T[state.inst_def[area]] != FX_03_085:
            continue
        if total_power(state, player) < effective_power_cost(state, area):
            continue
        if player.flags[PF_DAMAGE_TAKEN] >= 30:
            player.set_c = -1
            place_in_abyss(state, area, player.index, player.index)
        elif not state.is_night:
            set_chronos(state, (state.chronos + 2) % CHRONOS_SIZE)

    healed = False
    for player in state.players:
        area = player.set_c
        if area == -1 or EFFECT_T[state.inst_def[area]] != FX_03_058:
            continue
        if total_power(state, player) < effective_power_cost(state, area):
            continue
        if player.flags[PF_DAMAGE_TAKEN] >= 30:
            player.set_c = -1
            place_in_abyss(state, area, player.index, player.index)
        elif not healed:
            for heal_index in (0, 1):
                heal_player = state.players[heal_index]
                old_hp = heal_player.hp
                heal_player.hp = min(100, heal_player.hp + 10)
                if state.event_sink is not None and heal_player.hp != old_hp:
                    state.event_sink.append(
                        (EVENT_HP_CHANGED, heal_index, heal_player.hp - old_hp,
                         heal_player.hp))
            healed = True
