"""Area-enchant removal conditions (old check_area_enchant_removal).

Called after CHARACTER_SWAP, after AREA_SWAP, and at END_TURN with
end_of_turn=True. Area enchants whose power cost is unmet are never removed
(Q&A rule). Removal routes through zones so placement triggers fire; 04-030
is forced to the abyss by its card text despite having SEND TO POWER.
"""

from __future__ import annotations

from ..cards import EFFECT_T, EFFECT_TO_INDEX, POWER_COST_T, SONG_T, SONG_NAMES
from ..state import (
    GameState,
    PF_CHAR_TO_POWER, PF_OPP_CARD_TO_ABYSS, PF_ABYSS_RECEIVED,
    PF_CARD_TO_POWER, PF_BATTLE_LOST, PF_DAMAGE_TAKEN,
    GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY,
)
from ..zones import place_in_abyss, to_power_or_abyss

_FX = EFFECT_TO_INDEX
FX_02_005 = _FX["02-005"]
FX_02_007 = _FX["02-007"]
FX_03_058 = _FX["03-058"]
FX_03_085 = _FX["03-085"]
FX_02_058 = _FX["02-058"]
FX_02_064 = _FX["02-064"]
FX_02_086 = _FX["02-086"]
FX_02_092 = _FX["02-092"]
FX_02_098 = _FX["02-098"]
FX_02_104 = _FX["02-104"]
FX_03_055 = _FX["03-055"]
FX_03_061 = _FX["03-061"]
FX_03_064 = _FX["03-064"]
FX_03_086 = _FX["03-086"]
FX_03_091 = _FX["03-091"]
FX_03_092 = _FX["03-092"]
FX_03_098 = _FX["03-098"]
FX_03_104 = _FX["03-104"]
FX_04_030 = _FX["04-030"]
FX_04_032 = _FX["04-032"]
FX_04_033 = _FX["04-033"]
FX_04_065 = _FX["04-065"]
FX_04_091 = _FX["04-091"]
FX_04_094 = _FX["04-094"]
FX_04_095 = _FX["04-095"]

SONG_STUDY_ME = SONG_NAMES.index("STUDY_ME")

_ABYSS_AT_4 = (FX_03_086, FX_03_092, FX_03_098, FX_03_104)


def on_area_enchant_leaves_play(state: GameState, area_instance: int, owner_index: int) -> None:
    """Clean up persistent state tied to an area enchant on every removal path."""
    if EFFECT_T[state.inst_def[area_instance]] == FX_03_055:
        state.players[1 - owner_index].area_blocked = False


#: Area enchants whose end condition is an HP/damage threshold worded 「すぐに」, so it
#: must be evaluated the instant HP changes rather than at the next phase boundary
#: (Q&A No.16 for 03-058/03-085, Q&A No.80 for 04-091: 「HPの処理を終えたらすぐに」).
_DAMAGE_TRIGGERED = (FX_03_058, FX_03_085, FX_04_091)


def check_area_removal(state: GameState, *, end_of_turn: bool = False,
                       damage_only: bool = False) -> None:
    """Evaluate area-enchant end conditions.

    `damage_only` restricts the pass to the HP/damage-triggered cards above. It is
    used by the hooks that fire the moment HP changes, so those three cards leave play
    at the right instant without dragging the other seventeen predicates onto a new
    timing they were never audited against.
    """
    from ..battle import effective_power_cost, total_power  # avoid import cycle

    for player in state.players:
        area = player.set_c
        if area == -1:
            continue
        if total_power(state, player) < effective_power_cost(state, area):
            continue

        effect = EFFECT_T[state.inst_def[area]]
        if damage_only and effect not in _DAMAGE_TRIGGERED:
            continue
        opponent = state.players[1 - player.index]
        remove = False

        if effect == FX_02_005:
            remove = end_of_turn and bool(state.gflags[GF_DAY_TO_NIGHT] or state.gflags[GF_NIGHT_TO_DAY])
        elif effect == FX_02_007:
            remove = (end_of_turn and opponent.set_c != -1
                      and bool(state.inst_played[opponent.set_c]))
        elif effect == FX_02_058:
            remove = end_of_turn and bool(player.flags[PF_CHAR_TO_POWER])
        elif effect == FX_02_064:
            remove = end_of_turn and opponent.hp <= 30
        elif effect == FX_02_086:
            remove = bool(state.gflags[GF_NIGHT_TO_DAY]) or not state.is_night
        elif effect == FX_02_092:
            remove = opponent.battle != -1 and POWER_COST_T[state.inst_def[opponent.battle]] >= 4
        elif effect == FX_02_098:
            remove = bool(state.gflags[GF_DAY_TO_NIGHT]) or state.is_night
        elif effect == FX_02_104:
            remove = player.battle != -1 and POWER_COST_T[state.inst_def[player.battle]] >= 4
        elif effect == FX_03_055:
            remove = end_of_turn and bool(player.flags[PF_OPP_CARD_TO_ABYSS])
        elif effect == FX_03_061:
            remove = end_of_turn and opponent.set_c != -1
        elif effect == FX_03_064:
            remove = end_of_turn and opponent.hp <= 40
        elif effect == FX_03_091:
            remove = end_of_turn and bool(player.flags[PF_OPP_CARD_TO_ABYSS])
        elif effect in _ABYSS_AT_4:
            remove = end_of_turn and len(player.abyss) >= 4
        elif effect == FX_04_030:
            remove = bool(opponent.flags[PF_ABYSS_RECEIVED])
        elif effect == FX_04_032:
            remove = opponent.set_c != -1
        elif effect == FX_04_033:
            remove = bool(player.flags[PF_CARD_TO_POWER])
        elif effect == FX_04_065:
            if player.swapped_from_songs:  # a swap happened this turn
                remove = (player.battle == -1
                          or SONG_T[state.inst_def[player.battle]] != SONG_STUDY_ME)
        elif effect == FX_04_091:
            remove = player.hp <= 50
        elif effect == FX_04_094:
            remove = len(player.charger) >= 5
        elif effect == FX_04_095:
            remove = bool(player.flags[PF_BATTLE_LOST])
        elif effect in (FX_03_058, FX_03_085):
            # 「30ダメージ以上を受けたなら、すぐにアビスに置く」. Q&A No.16: because the
            # text says すぐに, the card reaches the abyss in the gap between
            # taking the damage and the turn-end processing, so its turn-end
            # block never runs. Removing it here rather than inside
            # process_end_of_turn_effects also keeps it out of play for anything
            # that reads the zone in between (an opponent's 04-032, say).
            remove = player.flags[PF_DAMAGE_TAKEN] >= 30

        if remove:
            player.set_c = -1
            on_area_enchant_leaves_play(state, area, player.index)
            if effect == FX_04_030:
                place_in_abyss(state, area, player.index, player.index)
            else:
                to_power_or_abyss(state, area, player.index)
