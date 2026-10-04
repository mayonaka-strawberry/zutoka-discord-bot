"""Effect IR: the declarative form of every dispatchable effect, run by
interpreter.py and featurized by features.py.

EffectIR(effect_id, family, cond, ops, custom):
- cond: condition tree gating the effect at resolution (conditions.py); None = always.
  Valid names: catalog._COND_NAMES, plus 'and', 'or', 'not'.
- ops: linear op tuples with explicit jump targets. Valid names: interpreter.OP_TABLE.
- custom: a custom.py handler that runs instead of ops; cond and ops still describe
  the effect for the featurizer.

Sides are relative to the owner (SELF = 0, OPP = 1). Expressions: see interpreter.eval_expr.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SELF = 0
OPP = 1


@dataclass(frozen=True)
class Sel:
    """Card selector. Results keep zone order; attribute filters use the
    effective attribute (02-084)."""
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
    family: str               # catalog family letter(s)
    cond: tuple | None = None
    ops: tuple = ()
    custom: str | None = None
    # Passives the engine applies inline (02-005, 02-007, 02-062): featurized, never dispatched.
    inline: bool = False
    notes: str = ""           # documentation only


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
