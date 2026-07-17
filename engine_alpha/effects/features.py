"""Auto-featurizer: EffectIR -> fixed 128-d float vector.

The features are derived purely from the IR (the same artifact the
interpreter executes), so the network's effect representation can never
drift from behavior. Precomputed once into EFFECT_FEATURES[NUM_EFFECTS+1,
128]; the last row is the "no effect" vector (all zeros).

Layout (128 dims):
  [0:3]    entry flags: has_custom, inline, is_area_enchant_carrier
  [3:8]    condition class: unconditional, has_cond, and/or/not present
  [8:40]   condition-leaf multi-hot (32 kinds)
  [40:45]  condition attribute one-hot (which attribute any condition tests)
  [45:48]  condition side flags (self, opp) + numeric-threshold presence
  [48:52]  condition scalars: cost/24, hp/100, count/8, distinct/4
  [52:92]  op-verb multi-hot (40 verbs)
  [92:97]  op target-side flags: buffs-self, debuffs-opp, damages-opp,
           heals-self, moves-opp-cards
  [97:107] magnitude scalars: atk-delta/200 (signed), heal/50, damage/50,
           draw/5, mill/6, chronos/18, cost-reduction/4, power-bonus/8,
           bank-count/6, per-card-multiplier/50
  [107:117] selector summary: zone multi-hot (6) + has-attr-filter,
            has-song-filter, has-type-filter, has-stp-filter
  [117:123] choice profile: has-pick-card, has-number, has-multiselect,
            has-name-guess, has-chronos-pick, max-picks/7
  [123:128] reserved (zero)
"""

from __future__ import annotations

import numpy as np

from ..cards import NUM_EFFECTS, EFFECT_TO_INDEX, CARD_DB, TYPE_AREA_ENCHANT
from .catalog import CATALOG
from .ir import EffectIR, Sel

FEATURE_DIM = 128

_COND_KINDS = (
    "enemy_attr", "own_attr", "enemy_cost_ge", "enemy_cost_le",
    "enemy_cost_eq_own", "enemy_stp_eq", "enemy_atk_eq0",
    "enemy_atk_eq0_no_override", "time", "midnight", "transition",
    "turn_became", "own_hp_le", "hp_lt_opp", "opp_hp_eq",
    "charger_all_attr", "charger_has_attr", "charger_attr_count_ge",
    "charger_distinct_attr_ge", "charger_count_le",
    "abyss_all_attr", "abyss_attr_count_ge", "abyss_distinct_attr_ge",
    "abyss_count_ge", "abyss_empty", "prev_char_attr", "own_cost_ge",
    "own_cost_le", "swapped_from_song", "swapped_any", "battle_song",
    "opp_has_area",
)
_COND_INDEX = {k: i for i, k in enumerate(_COND_KINDS)}

_OP_VERBS = (
    "end", "jump", "if_not", "if_reg_empty", "if_reg_le",
    "pick_card", "pick_number", "multiselect", "picks_exact", "name_guess",
    "pick_chronos", "atk_bonus", "dmg_reduce", "atk_override",
    "not_reducible", "reverse_day_night", "power_bonus", "heal", "damage",
    "eot_damage", "reflect", "adv_chronos", "set_chronos_to",
    "midnight_extend", "draw", "draw_exact", "move_reg", "mill",
    "deck_top_route", "chronos_revert_turn_start", "chronos_back_opp_clock",
    "bounce_opp_area", "reveal_reg", "reveal_hand", "shuffle_hand",
    "hand_bonus", "attr_override_enemy", "negate_reg", "block_area",
    "cost_reduce_set_chars",
)
_OP_INDEX = {k: i for i, k in enumerate(_OP_VERBS)}
_ZONES = ("hand", "charger", "abyss", "deck", "battle", "set_c")


def _walk_cond(cond, out: np.ndarray) -> None:
    if cond is None:
        return
    kind = cond[0]
    if kind in ("and", "or", "not"):
        out[5 if kind == "and" else 6 if kind == "or" else 7] = 1.0
        for sub in (cond[1:] if kind != "not" else (cond[1],)):
            _walk_cond(sub, out)
        return
    out[4] = 1.0
    if kind in _COND_INDEX:
        out[8 + _COND_INDEX[kind]] = 1.0
    # attribute argument (position varies; scan small ints 0..4 after side args)
    if kind in ("enemy_attr", "own_attr"):
        out[40 + cond[1]] = 1.0
        out[45 if kind == "own_attr" else 46] = 1.0
    elif kind in ("charger_all_attr", "charger_has_attr", "abyss_all_attr", "prev_char_attr"):
        out[40 + cond[2]] = 1.0
        out[45 if cond[1] == 0 else 46] = 1.0
    elif kind in ("charger_attr_count_ge", "abyss_attr_count_ge", "hand_attr_count_ge"):
        out[40 + cond[2]] = 1.0
        out[45 if cond[1] == 0 else 46] = 1.0
        out[47] = 1.0
        out[50] = max(out[50], cond[3] / 8.0)
    if kind in ("enemy_cost_ge", "enemy_cost_le", "own_cost_ge", "own_cost_le"):
        out[47] = 1.0
        out[48] = max(out[48], cond[1] / 24.0)
    if kind in ("own_hp_le", "opp_hp_eq"):
        out[47] = 1.0
        out[49] = max(out[49], cond[1] / 100.0)
    if kind in ("abyss_count_ge", "charger_count_le", "deck_ge", "hand_count_ge"):
        out[47] = 1.0
        out[50] = max(out[50], cond[2] / 8.0)
    if kind in ("charger_distinct_attr_ge", "abyss_distinct_attr_ge", "hand_distinct_attr_ge"):
        out[51] = max(out[51], cond[2] / 4.0)


def _expr_magnitude(expr) -> tuple[float, float]:
    """Returns (flat_amount, per_card_multiplier)."""
    if isinstance(expr, int):
        return float(expr), 0.0
    if expr[0] == "mul":
        return 0.0, float(expr[2])
    return 0.0, 1.0  # reg/count-driven


def _featurize(entry: EffectIR) -> np.ndarray:
    out = np.zeros(FEATURE_DIM, dtype=np.float32)
    out[0] = 1.0 if entry.custom else 0.0
    out[1] = 1.0 if entry.inline else 0.0
    carrier = CARD_DB[EFFECT_TO_INDEX[entry.effect_id]]
    # carrier index == effect card: effects are unique per card
    from ..cards import EFFECT_TO_CARD
    carrier = CARD_DB[EFFECT_TO_CARD[EFFECT_TO_INDEX[entry.effect_id]]]
    out[2] = 1.0 if carrier.card_type == TYPE_AREA_ENCHANT else 0.0
    out[3] = 1.0 if entry.cond is None else 0.0
    _walk_cond(entry.cond, out)

    max_picks = 0.0
    for op in entry.ops:
        verb = op[0]
        if verb in _OP_INDEX:
            out[52 + _OP_INDEX[verb]] = 1.0
        if verb == "if_not":
            _walk_cond(op[1], out)
        if verb == "atk_bonus":
            amount, per_card = _expr_magnitude(op[2])
            if op[1] == 0:
                out[92] = 1.0
                out[97] = max(out[97], amount / 200.0)
            else:
                out[93] = 1.0
                out[97] = min(out[97], amount / 200.0) if amount < 0 else out[97]
            out[106] = max(out[106], per_card / 50.0)
        elif verb == "heal":
            amount, _ = _expr_magnitude(op[2])
            out[95] = 1.0
            out[98] = max(out[98], amount / 50.0)
        elif verb in ("damage", "eot_damage"):
            amount, _ = _expr_magnitude(op[2])
            out[94] = 1.0
            out[99] = max(out[99], amount / 50.0)
        elif verb in ("draw", "draw_exact"):
            amount, _ = _expr_magnitude(op[2])
            out[100] = max(out[100], amount / 5.0 if amount else 0.4)
        elif verb == "mill":
            amount, _ = _expr_magnitude(op[2])
            out[101] = max(out[101], amount / 6.0 if amount else 0.5)
        elif verb in ("adv_chronos", "set_chronos_to"):
            amount, _ = _expr_magnitude(op[1])
            out[102] = max(out[102], abs(amount) / 18.0)
        elif verb in ("cost_reduce_set_chars",):
            out[103] = max(out[103], op[1] / 4.0)
        elif verb == "cost_reduce_battle_song":
            out[52 + _OP_INDEX["cost_reduce_set_chars"]] = 1.0
            out[103] = max(out[103], op[2] / 4.0)
        elif verb == "power_bonus":
            amount, per_card = _expr_magnitude(op[2])
            out[104] = max(out[104], amount / 8.0, per_card / 8.0)
        elif verb == "dmg_reduce":
            amount, _ = _expr_magnitude(op[2])
            out[105] = max(out[105], amount / 200.0)
        elif verb == "move_reg" and len(op) >= 4 and op[3] == 1:
            out[96] = 1.0  # moves opponent cards

        for arg in op[1:]:
            if isinstance(arg, Sel):
                if arg.zone in _ZONES:
                    out[107 + _ZONES.index(arg.zone)] = 1.0
                if arg.attribute != -1:
                    out[113] = 1.0
                if arg.song != -1:
                    out[114] = 1.0
                if arg.card_type != -1:
                    out[115] = 1.0
                if arg.stp_ge != -1 or arg.stp_eq != -1:
                    out[116] = 1.0
            elif isinstance(arg, tuple) and arg and arg[0] == "count" and isinstance(arg[1], Sel):
                sel = arg[1]
                if sel.zone in _ZONES:
                    out[107 + _ZONES.index(sel.zone)] = 1.0
                if sel.attribute != -1:
                    out[113] = 1.0
                if sel.song != -1:
                    out[114] = 1.0

        if verb == "pick_card":
            out[117] = 1.0
            max_picks = max(max_picks, 1.0)
        elif verb in ("pick_number",):
            out[118] = 1.0
        elif verb in ("multiselect", "picks_exact"):
            out[119] = 1.0
            max_picks = max(max_picks, 4.0)
        elif verb == "name_guess":
            out[120] = 1.0
        elif verb == "pick_chronos":
            out[121] = 1.0
    out[122] = max_picks / 7.0
    return out


def build_effect_features() -> np.ndarray:
    features = np.zeros((NUM_EFFECTS + 1, FEATURE_DIM), dtype=np.float32)
    for effect_id, entry in CATALOG.items():
        features[EFFECT_TO_INDEX[effect_id]] = _featurize(entry)
    return features


EFFECT_FEATURES = build_effect_features()
