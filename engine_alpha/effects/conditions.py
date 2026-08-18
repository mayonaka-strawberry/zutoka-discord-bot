"""Condition-tree evaluation against the game state.

All sides are relative to the effect owner (0 = owner, 1 = opponent).
Attribute checks use the effective attribute (02-084 overrides).
Cost checks on battle characters use the printed power_cost (matching the
old effect implementations, which read card.power_cost directly).
"""

from __future__ import annotations

from ..cards import ATTRIBUTE_T, POWER_COST_T, SEND_TO_POWER_T, SONG_T
from ..state import GameState, GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY
from .selectors import effective_attribute


def _player(state: GameState, owner_index: int, side: int):
    return state.players[owner_index if side == 0 else 1 - owner_index]


def _battle_def(state: GameState, player) -> int:
    return state.inst_def[player.battle] if player.battle != -1 else -1


def _distinct_attrs(state: GameState, instance_ids: list[int]) -> int:
    return len({effective_attribute(state, i) for i in instance_ids})


def _attr_count(state: GameState, instance_ids: list[int], attr: int) -> int:
    return sum(1 for i in instance_ids if effective_attribute(state, i) == attr)


def eval_cond(state: GameState, owner_index: int, cond) -> bool:
    if cond is None:
        return True
    kind = cond[0]

    if kind == "and":
        return all(eval_cond(state, owner_index, sub) for sub in cond[1:])
    if kind == "or":
        return any(eval_cond(state, owner_index, sub) for sub in cond[1:])
    if kind == "not":
        return not eval_cond(state, owner_index, cond[1])

    own = state.players[owner_index]
    enemy = state.players[1 - owner_index]

    if kind == "enemy_attr":
        return enemy.battle != -1 and effective_attribute(state, enemy.battle) == cond[1]
    if kind == "own_attr":
        return own.battle != -1 and effective_attribute(state, own.battle) == cond[1]
    if kind == "enemy_cost_ge":
        return enemy.battle != -1 and POWER_COST_T[_battle_def(state, enemy)] >= cond[1]
    if kind == "enemy_cost_le":
        return enemy.battle != -1 and POWER_COST_T[_battle_def(state, enemy)] <= cond[1]
    if kind == "enemy_cost_eq_own":
        return (enemy.battle != -1 and own.battle != -1
                and POWER_COST_T[_battle_def(state, enemy)] == POWER_COST_T[_battle_def(state, own)])
    if kind == "enemy_stp_eq":
        return enemy.battle != -1 and SEND_TO_POWER_T[_battle_def(state, enemy)] == cond[1]
    if kind == "enemy_atk_eq0":
        # Q&A No.60: covers a printed 0, no character set, and an unmet power
        # cost alike -- anything that leaves the enemy unable to attack. All
        # four cards with this text (04-034/04-039/04-084/04-101) use it; the
        # old engine's 04-084/04-101 variant that skipped the 04-099 set was an
        # inlining artifact with no basis in the rulings.
        from ..battle import get_effective_attack
        return get_effective_attack(state, enemy) == 0
    if kind == "time":
        return state.is_night == (cond[1] == "night")
    if kind == "midnight":
        from ..battle import is_effectively_midnight
        return is_effectively_midnight(state)
    if kind == "transition":
        flag = GF_DAY_TO_NIGHT if cond[1] == "d2n" else GF_NIGHT_TO_DAY
        return bool(state.gflags[flag])
    if kind == "turn_became":
        # Official Q&A No.17/No.18: the effect fires when the named crossing
        # happened AT LEAST ONCE this turn, even if the clock ends the turn in
        # the period it started in. Q&A No.18's example runs night -> day ->
        # night in one turn and still activates a "day changes to night" card,
        # so comparing chronos_at_turn_start against the current period (what
        # the old code did) is wrong. advance_chronos_by records each crossing
        # in the shared flags, which is exactly what this needs.
        flag = GF_NIGHT_TO_DAY if cond[1] == "day" else GF_DAY_TO_NIGHT
        return bool(state.gflags[flag])
    if kind == "own_hp_le":
        return own.hp <= cond[1]
    if kind == "hp_lt_opp":
        return own.hp < enemy.hp
    if kind == "opp_hp_eq":
        return enemy.hp == cond[1]

    if kind == "charger_all_attr":
        charger = _player(state, owner_index, cond[1]).charger
        return bool(charger) and _attr_count(state, charger, cond[2]) == len(charger)
    if kind == "charger_has_attr":
        return _attr_count(state, _player(state, owner_index, cond[1]).charger, cond[2]) >= 1
    if kind == "charger_attr_count_ge":
        return _attr_count(state, _player(state, owner_index, cond[1]).charger, cond[2]) >= cond[3]
    if kind == "charger_distinct_attr_ge":
        return _distinct_attrs(state, _player(state, owner_index, cond[1]).charger) >= cond[2]
    if kind == "charger_count_le":
        return len(_player(state, owner_index, cond[1]).charger) <= cond[2]

    if kind == "abyss_all_attr":
        abyss = _player(state, owner_index, cond[1]).abyss
        return bool(abyss) and _attr_count(state, abyss, cond[2]) == len(abyss)
    if kind == "abyss_attr_count_ge":
        return _attr_count(state, _player(state, owner_index, cond[1]).abyss, cond[2]) >= cond[3]
    if kind == "abyss_distinct_attr_ge":
        return _distinct_attrs(state, _player(state, owner_index, cond[1]).abyss) >= cond[2]
    if kind == "abyss_count_ge":
        return len(_player(state, owner_index, cond[1]).abyss) >= cond[2]
    if kind == "abyss_empty":
        return not _player(state, owner_index, cond[1]).abyss

    if kind == "prev_char_attr":
        prev_def = _player(state, owner_index, cond[1]).prev_battle_def
        return prev_def != -1 and ATTRIBUTE_T[prev_def] == cond[2]
    if kind == "own_cost_ge":
        return own.battle != -1 and POWER_COST_T[_battle_def(state, own)] >= cond[1]
    if kind == "own_cost_le":
        return own.battle != -1 and POWER_COST_T[_battle_def(state, own)] <= cond[1]
    if kind == "swapped_from_song":
        return bool(own.swapped_from_songs & (1 << cond[1]))
    if kind == "swapped_any":
        return own.swapped_from_songs != 0
    if kind == "battle_song":
        player = _player(state, owner_index, cond[1])
        return player.battle != -1 and SONG_T[state.inst_def[player.battle]] == cond[2]
    if kind == "opp_has_area":
        return enemy.set_c != -1
    if kind == "own_battle_played":
        return own.battle != -1 and bool(state.inst_played[own.battle])
    # Currently used by ZERO catalog entries, and that is deliberate: the 2026-08
    # audit stripped it off 01-092/04-089, where an "if you can draw" gate with no
    # basis in the card text silently turned an impossible draw into a no-op
    # instead of the Ground Rules 8.2.1 loss. Kept because the primitive itself is
    # sound -- but a new entry using it needs the card text to actually say "if".
    if kind == "deck_ge":
        return len(_player(state, owner_index, cond[1]).deck) >= cond[2]
    if kind == "hand_count_ge":
        return len(_player(state, owner_index, cond[1]).hand) >= cond[2]
    if kind == "hand_attr_count_ge":
        return _attr_count(state, _player(state, owner_index, cond[1]).hand, cond[2]) >= cond[3]
    if kind == "hand_distinct_attr_ge":
        return _distinct_attrs(state, _player(state, owner_index, cond[1]).hand) >= cond[2]

    raise ValueError(f"unknown condition kind {kind!r}")
