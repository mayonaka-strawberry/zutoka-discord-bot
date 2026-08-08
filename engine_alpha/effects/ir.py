"""Effect IR: the declarative language all 250 dispatchable effects are
written in. The IR is simultaneously the implementation (interpreted by
interpreter.py) and the network representation (featurized by features.py).

An effect entry is:

    EffectIR(effect_id, family, cond, ops, custom)

- cond: a condition tree (nested tuples, conditions.py) gating the whole
  effect; None = unconditional. Evaluated at resolution time.
- ops: a linear tuple of op tuples (interpreter.py) with explicit jump
  targets for the few branching effects.
- custom: name of a handler in custom.py that replaces ops execution for
  effects whose semantics don't fit the linear form. cond/ops still hold
  the best structured *description* for the featurizer.

Sides are relative to the effect owner: SELF = 0, OPP = 1.

Condition leaves (conditions.py):
  ('enemy_attr', attr)            ('own_attr', attr)
  ('enemy_cost_ge', n)            ('enemy_cost_le', n)
  ('enemy_cost_eq_own',)          ('enemy_stp_eq', n)
  ('enemy_atk_eq0',)
  ('time', 'day'|'night')         ('midnight',)
  ('transition', 'd2n'|'n2d')
  ('own_hp_le', n)                ('hp_lt_opp',)
  ('opp_hp_eq', n)
  ('charger_all_attr', side, attr)      # non-empty, every card that attr
  ('charger_has_attr', side, attr)
  ('charger_attr_count_ge', side, attr, n)
  ('charger_distinct_attr_ge', side, n)
  ('charger_count_le', side, n)
  ('abyss_all_attr', side, attr)
  ('abyss_attr_count_ge', side, attr, n)
  ('abyss_distinct_attr_ge', side, n)
  ('abyss_count_ge', side, n)     ('abyss_empty', side)
  ('prev_char_attr', side, attr)
  ('own_cost_ge', n)              ('own_cost_le', n)
  ('swapped_from_song', song)     ('swapped_any',)
  ('battle_song', side, song)     # side's battle character sings `song`
  ('opp_has_area',)
  ('hand_attr_count_ge', side, attr, n)
  ('and', c...) ('or', c...) ('not', c)

Expressions (interpreter.py eval_expr):
  int literal n
  ('reg', i)          value of int register i
  ('reg_len', i)      length of list register i
  ('mul', expr, k)
  ('count', sel)      number of cards matching selector
  ('hand_len', side)  ('charger_len', side) ('abyss_len', side)

Ops (interpreter.py OP_TABLE); `side` relative to owner:
  control:   ('if_not', cond, target_pc)  ('jump', target_pc)  ('end',)
             ('if_reg_empty', reg, target_pc) ('if_reg_le', reg, n, target_pc)
  choices:   ('pick_card', reg, sel)               -> SelectCard (empty sel aborts effect)
             ('pick_card_opt', reg, sel, skip_target)  -> declinable SelectCard ('may');
                 PASS or empty sel jumps to skip_target (skips dependent ops)
             ('pick_number', reg, lo, hi_expr)     -> SelectNumber
             ('multiselect', reg, sel, min_cards)  -> SelectNumber then k SelectCards
             ('picks_exact', reg, sel, count_expr) -> count sequential SelectCards
             ('name_guess', reg)                   -> SelectIdentity
  buffs:     ('atk_bonus', side, expr)   ('dmg_reduce', side, expr)
             ('atk_override', side, expr) ('not_reducible', side)
             ('reverse_day_night', side) ('power_bonus', side, expr)
  hp:        ('heal', side, expr)  ('damage', side, expr)
             ('eot_damage', side, expr)  ('reflect', side)
  clock:     ('adv_chronos', expr) ('set_chronos_to', expr) ('midnight_extend',)
  cards:     ('draw', side, expr)              min(expr, deck) drawn
             ('move_reg', reg, dst_zone, dst_side, order)
                 dst_zone in 'abyss'|'charger'|'deck'|'hand'; order 'top'|'bottom'
                 (actor for placement triggers is always the effect owner)
             ('mill', side, expr)              top N of side's deck -> side's abyss
             ('charger_to_abyss', side)        04-105: empty side's charger into
                 that side's OWN abyss (actor = effect owner)
             ('reveal_reg', reg)  ('reveal_hand', side)
                 informational: mutate nothing, emit EVENT_CARDS_REVEALED for
                 the driver to show. reveal_reg fires even on an empty reg.
             ('shuffle_hand', side)            rng event
             ('hand_bonus', side)              pending hand-size bonus +1
  area:      ('bounce_opp_area', order, cleanup)   opponent's area -> their deck
             ('opp_area_to_abyss',)            04-107: opponent's area -> their
                 abyss, forced even with SEND TO POWER; fires leave-play cleanup
  misc:      ('attr_override_enemy', attr)     opponent battle char attribute
             ('negate_reg', reg)  ('block_area', side)
             ('cost_reduce_set_chars', n)      02-006: set A/B/battle chars played
             ('cost_reduce_battle_song', song, n)  04-065: battle char of song
"""

from __future__ import annotations

from dataclasses import dataclass, field

SELF = 0
OPP = 1


@dataclass(frozen=True)
class Sel:
    """Card selector: which instances an op looks at / a player picks from.

    Evaluation preserves zone order (hand order, charger order, ...).
    Filters use the *effective* attribute (respects 02-084 overrides).
    """
    side: int                 # SELF / OPP (relative to effect owner)
    zone: str                 # 'hand' | 'charger' | 'abyss' | 'deck' | 'battle' | 'set_c'
    card_type: int = -1       # cards.TYPE_* or -1 = any
    attribute: int = -1       # cards.ATTR_* or -1 = any
    song: int = -1            # song index or -1 = any
    stp_ge: int = -1          # send_to_power >= n (-1 = no filter)
    stp_eq: int = -1
    cost_ge: int = -1
    top_n: int = 0            # restrict to top N of an ordered zone (deck)


@dataclass(frozen=True)
class EffectIR:
    effect_id: str            # "03-045"
    family: str               # catalog family letter(s), for featurizer/bookkeeping
    cond: tuple | None = None
    ops: tuple = ()
    custom: str | None = None
    # Passive effects (02-005/02-007/02-062/03-061 style) acted on inline by
    # the engine; not dispatchable. Featurizer-only entries.
    inline: bool = False
    notes: str = ""           # ruling notes / description for auditability


def validate_ir(entry: EffectIR, op_names: frozenset[str], cond_names: frozenset[str]) -> None:
    def walk_cond(cond) -> None:
        if cond is None:
            return
        kind = cond[0]
        if kind in ("and", "or"):
            for sub in cond[1:]:
                walk_cond(sub)
        elif kind == "not":
            walk_cond(cond[1])
        elif kind not in cond_names:
            raise ValueError(f"{entry.effect_id}: unknown condition {kind!r}")

    walk_cond(entry.cond)
    for pc, op in enumerate(entry.ops):
        if op[0] not in op_names:
            raise ValueError(f"{entry.effect_id}: unknown op {op[0]!r} at {pc}")
        if op[0] in ("if_not", "jump", "if_reg_empty", "if_reg_le", "pick_card_opt"):
            target = op[-1]
            if not 0 <= target <= len(entry.ops):
                raise ValueError(f"{entry.effect_id}: jump target {target} out of range at {pc}")
