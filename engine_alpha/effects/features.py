"""Auto-featurizer: EffectIR -> fixed 160-d float vector.

The features are derived purely from the IR (the same artifact the
interpreter executes), so the network's effect representation can never
drift from behavior. Precomputed once into EFFECT_FEATURES[NUM_EFFECTS+1,
160]; the last row is the "no effect" vector (all zeros).

The verb and condition blocks are sized to cover interpreter.OP_TABLE and
catalog._COND_NAMES *in full* — test_rulings asserts that, because a verb
missing from the block is silently dropped by the `if verb in _OP_INDEX`
guard rather than raising, which is how five verbs once rotted out of it.

Layout (160 dims):
  [0:3]     entry flags: has_custom, inline, is_area_enchant_carrier
  [3:8]     condition class: unconditional, has_cond, and/or/not present
  [8:45]    condition-leaf multi-hot (37 kinds)
  [45:50]   condition attribute one-hot (which attribute any condition tests)
  [50:53]   condition side flags (self, opp) + numeric-threshold presence
  [53:57]   condition scalars: cost/24, hp/100, count/8, distinct/4
  [57:105]  op-verb multi-hot (48 verbs)
  [105:110] op target-side flags: buffs-self, debuffs-opp, damages-opp,
            heals-self, moves-opp-cards
  [110:121] magnitude scalars: atk-delta/200 (signed), heal/50, damage/50,
            draw/5, mill/6, chronos/18, cost-reduction/4, power-bonus/8,
            damage-reduction/200, per-card-multiplier/50, bank-count/8
  [121:131] selector summary: zone multi-hot (6) + has-attr-filter,
            has-song-filter, has-type-filter, has-stp-filter
  [131:138] choice profile: has-pick-card, has-optional-pick, has-number,
            has-multiselect, has-name-guess, has-chronos-pick, max-picks/8
  [138:160] reserved (zero)
"""

from __future__ import annotations

import numpy as np

from ..cards import NUM_EFFECTS, EFFECT_TO_INDEX, CARD_DB, TYPE_AREA_ENCHANT
from .catalog import CATALOG
from .ir import EffectIR, Sel

FEATURE_DIM = 160

# --- block offsets (single source of truth for the layout above) -----------
COND_LEAF = 8
COND_ATTR = 45
COND_SIDE_SELF, COND_SIDE_OPP, COND_NUMERIC = 50, 51, 52
COND_SCALAR_COST, COND_SCALAR_HP, COND_SCALAR_COUNT, COND_SCALAR_DISTINCT = 53, 54, 55, 56
OP_VERB = 57
TARGET_BUFF_SELF, TARGET_DEBUFF_OPP = 105, 106
TARGET_DAMAGE_OPP, TARGET_HEAL_SELF, TARGET_MOVES_OPP = 107, 108, 109
MAG_ATTACK, MAG_HEAL, MAG_DAMAGE, MAG_DRAW, MAG_MILL, MAG_CHRONOS = 110, 111, 112, 113, 114, 115
MAG_COST_REDUCTION, MAG_POWER_BONUS, MAG_DAMAGE_REDUCTION = 116, 117, 118
MAG_PER_CARD, MAG_BANK_COUNT = 119, 120
SEL_ZONE = 121
SEL_ATTR, SEL_SONG, SEL_TYPE, SEL_STP = 127, 128, 129, 130
CHOICE_PICK_CARD, CHOICE_OPTIONAL_PICK, CHOICE_NUMBER = 131, 132, 133
CHOICE_MULTISELECT, CHOICE_NAME_GUESS, CHOICE_CHRONOS, CHOICE_MAX_PICKS = 134, 135, 136, 137

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
    "opp_has_area", "own_battle_played", "deck_ge", "hand_count_ge",
    "hand_attr_count_ge", "hand_distinct_attr_ge",
)
_COND_INDEX = {k: i for i, k in enumerate(_COND_KINDS)}

_OP_VERBS = (
    "end", "jump", "if_not", "if_reg_empty", "if_reg_le",
    "pick_card", "pick_card_opt", "pick_number", "multiselect", "picks_exact",
    "name_guess", "pick_chronos", "atk_bonus", "dmg_reduce", "atk_override",
    "not_reducible", "reverse_day_night", "power_bonus", "heal", "damage",
    "eot_damage", "reflect", "adv_chronos", "set_chronos_to",
    "midnight_extend", "draw", "draw_exact", "move_reg", "mill",
    "deck_top_route", "chronos_revert_turn_start", "chronos_back_opp_clock",
    "bounce_opp_area", "opp_area_to_abyss", "charger_to_abyss",
    "name_guess_bonus", "negate_opp_set_enchants", "shuffle_reg", "lose_game",
    "reveal_reg", "reveal_hand", "shuffle_hand", "hand_bonus",
    "attr_override_enemy", "negate_reg", "block_area",
    "cost_reduce_set_chars", "cost_reduce_battle_song",
)
_OP_INDEX = {k: i for i, k in enumerate(_OP_VERBS)}
_ZONES = ("hand", "charger", "abyss", "deck", "battle", "set_c")

# Ops that always act on the opponent's cards regardless of a side argument.
_ALWAYS_MOVES_OPPONENT = ("bounce_opp_area", "opp_area_to_abyss",
                          "negate_opp_set_enchants", "attr_override_enemy")
# Ops whose first argument is a side, where OPP means "moves opponent cards".
_SIDED_MOVES_OPPONENT = ("mill", "charger_to_abyss", "deck_top_route")


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
        out[COND_LEAF + _COND_INDEX[kind]] = 1.0
    # attribute argument (position varies; scan small ints 0..4 after side args)
    if kind in ("enemy_attr", "own_attr"):
        out[COND_ATTR + cond[1]] = 1.0
        out[COND_SIDE_SELF if kind == "own_attr" else COND_SIDE_OPP] = 1.0
    elif kind in ("charger_all_attr", "charger_has_attr", "abyss_all_attr", "prev_char_attr"):
        out[COND_ATTR + cond[2]] = 1.0
        out[COND_SIDE_SELF if cond[1] == 0 else COND_SIDE_OPP] = 1.0
    elif kind in ("charger_attr_count_ge", "abyss_attr_count_ge", "hand_attr_count_ge"):
        out[COND_ATTR + cond[2]] = 1.0
        out[COND_SIDE_SELF if cond[1] == 0 else COND_SIDE_OPP] = 1.0
        out[COND_NUMERIC] = 1.0
        out[COND_SCALAR_COUNT] = max(out[COND_SCALAR_COUNT], cond[3] / 8.0)
    if kind in ("enemy_cost_ge", "enemy_cost_le", "own_cost_ge", "own_cost_le"):
        out[COND_NUMERIC] = 1.0
        out[COND_SCALAR_COST] = max(out[COND_SCALAR_COST], cond[1] / 24.0)
    if kind in ("own_hp_le", "opp_hp_eq"):
        out[COND_NUMERIC] = 1.0
        out[COND_SCALAR_HP] = max(out[COND_SCALAR_HP], cond[1] / 100.0)
    if kind in ("abyss_count_ge", "charger_count_le", "deck_ge", "hand_count_ge"):
        out[COND_NUMERIC] = 1.0
        out[COND_SCALAR_COUNT] = max(out[COND_SCALAR_COUNT], cond[2] / 8.0)
    if kind in ("charger_distinct_attr_ge", "abyss_distinct_attr_ge", "hand_distinct_attr_ge"):
        out[COND_SCALAR_DISTINCT] = max(out[COND_SCALAR_DISTINCT], cond[2] / 4.0)


def _expr_magnitude(expr) -> tuple[float, float]:
    """Returns (flat_amount, per_card_multiplier)."""
    if isinstance(expr, int):
        return float(expr), 0.0
    if expr[0] == "mul":
        return 0.0, float(expr[2])
    return 0.0, 1.0  # reg/count-driven


def _note_selector(sel: Sel, out: np.ndarray, *, filters: bool = True) -> None:
    if sel.zone in _ZONES:
        out[SEL_ZONE + _ZONES.index(sel.zone)] = 1.0
    if sel.attribute != -1:
        out[SEL_ATTR] = 1.0
    if sel.song != -1:
        out[SEL_SONG] = 1.0
    if not filters:
        return
    if sel.card_type != -1:
        out[SEL_TYPE] = 1.0
    if sel.stp_ge != -1 or sel.stp_eq != -1:
        out[SEL_STP] = 1.0


def _featurize(entry: EffectIR) -> np.ndarray:
    out = np.zeros(FEATURE_DIM, dtype=np.float32)
    out[0] = 1.0 if entry.custom else 0.0
    out[1] = 1.0 if entry.inline else 0.0
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
            out[OP_VERB + _OP_INDEX[verb]] = 1.0
        if verb == "if_not":
            _walk_cond(op[1], out)
        if verb in _ALWAYS_MOVES_OPPONENT:
            out[TARGET_MOVES_OPP] = 1.0
        elif verb in _SIDED_MOVES_OPPONENT and op[1] == 1:
            out[TARGET_MOVES_OPP] = 1.0

        if verb == "atk_bonus":
            amount, per_card = _expr_magnitude(op[2])
            if op[1] == 0:
                out[TARGET_BUFF_SELF] = 1.0
                out[MAG_ATTACK] = max(out[MAG_ATTACK], amount / 200.0)
            else:
                out[TARGET_DEBUFF_OPP] = 1.0
                if amount < 0:
                    out[MAG_ATTACK] = min(out[MAG_ATTACK], amount / 200.0)
            out[MAG_PER_CARD] = max(out[MAG_PER_CARD], per_card / 50.0)
        elif verb == "heal":
            amount, _ = _expr_magnitude(op[2])
            out[TARGET_HEAL_SELF] = 1.0
            out[MAG_HEAL] = max(out[MAG_HEAL], amount / 50.0)
        elif verb in ("damage", "eot_damage"):
            amount, _ = _expr_magnitude(op[2])
            out[TARGET_DAMAGE_OPP] = 1.0
            out[MAG_DAMAGE] = max(out[MAG_DAMAGE], amount / 50.0)
        elif verb in ("draw", "draw_exact"):
            amount, _ = _expr_magnitude(op[2])
            out[MAG_DRAW] = max(out[MAG_DRAW], amount / 5.0 if amount else 0.4)
        elif verb == "mill":
            amount, _ = _expr_magnitude(op[2])
            out[MAG_MILL] = max(out[MAG_MILL], amount / 6.0 if amount else 0.5)
        elif verb in ("adv_chronos", "set_chronos_to"):
            amount, _ = _expr_magnitude(op[1])
            out[MAG_CHRONOS] = max(out[MAG_CHRONOS], abs(amount) / 18.0)
        elif verb == "cost_reduce_set_chars":
            out[MAG_COST_REDUCTION] = max(out[MAG_COST_REDUCTION], op[1] / 4.0)
        elif verb == "cost_reduce_battle_song":
            out[MAG_COST_REDUCTION] = max(out[MAG_COST_REDUCTION], op[2] / 4.0)
        elif verb == "power_bonus":
            amount, per_card = _expr_magnitude(op[2])
            out[MAG_POWER_BONUS] = max(out[MAG_POWER_BONUS], amount / 8.0, per_card / 8.0)
        elif verb == "dmg_reduce":
            amount, _ = _expr_magnitude(op[2])
            out[MAG_DAMAGE_REDUCTION] = max(out[MAG_DAMAGE_REDUCTION], amount / 200.0)
        elif verb == "move_reg" and op[3] == 1:
            out[TARGET_MOVES_OPP] = 1.0

        for arg in op[1:]:
            if isinstance(arg, Sel):
                _note_selector(arg, out)
            elif isinstance(arg, tuple) and arg and arg[0] == "count" and isinstance(arg[1], Sel):
                _note_selector(arg[1], out, filters=False)

        if verb == "pick_card":
            out[CHOICE_PICK_CARD] = 1.0
            max_picks = max(max_picks, 1.0)
        elif verb == "pick_card_opt":
            out[CHOICE_OPTIONAL_PICK] = 1.0
            max_picks = max(max_picks, 1.0)
        elif verb == "pick_number":
            out[CHOICE_NUMBER] = 1.0
        elif verb in ("multiselect", "picks_exact"):
            out[CHOICE_MULTISELECT] = 1.0
            # A literal count is the real bank size (04-105 banks 8, 04-028 6);
            # a reg/expr-driven count is unknown at featurization time.
            count = op[3] if verb == "picks_exact" and isinstance(op[3], int) else 4
            max_picks = max(max_picks, float(count))
            out[MAG_BANK_COUNT] = max(out[MAG_BANK_COUNT], count / 8.0)
        elif verb == "name_guess":
            out[CHOICE_NAME_GUESS] = 1.0
        elif verb == "pick_chronos":
            out[CHOICE_CHRONOS] = 1.0
    out[CHOICE_MAX_PICKS] = max_picks / 8.0
    return out


def build_effect_features() -> np.ndarray:
    features = np.zeros((NUM_EFFECTS + 1, FEATURE_DIM), dtype=np.float32)
    for effect_id, entry in CATALOG.items():
        features[EFFECT_TO_INDEX[effect_id]] = _featurize(entry)
    return features


EFFECT_FEATURES = build_effect_features()
