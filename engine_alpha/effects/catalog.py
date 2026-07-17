"""IR catalog loader: validates every entry and compiles the interpreter
program table.

The entries themselves live in catalog_data.py (authored family-by-family
from the old engine's per-card implementations). This module:
- checks exactly one entry per effect id in the card DB,
- validates op/condition names and jump targets,
- registers programs with the interpreter,
- exposes DISPATCHABLE_EFFECTS (old handler registry minus inline passives)
  and COST_REDUCING_EFFECTS (old _COST_REDUCING_EFFECTS).
"""

from __future__ import annotations

from ..cards import EFFECT_IDS, EFFECT_TO_INDEX
from .conditions import eval_cond  # noqa: F401  (import validates module)
from .interpreter import CUSTOM_HANDLERS, EFFECT_PROGRAMS, OP_NAMES
from .ir import EffectIR, validate_ir
from . import custom as _custom_module  # registers CUSTOM_HANDLERS
from .catalog_data import ENTRIES

_COND_NAMES = frozenset({
    "enemy_attr", "own_attr", "enemy_cost_ge", "enemy_cost_le",
    "enemy_cost_eq_own", "enemy_stp_eq", "enemy_atk_eq0",
    "enemy_atk_eq0_no_override",
    "time", "midnight", "transition", "turn_became",
    "own_hp_le", "hp_lt_opp", "opp_hp_eq",
    "charger_all_attr", "charger_has_attr", "charger_attr_count_ge",
    "charger_distinct_attr_ge", "charger_count_le",
    "abyss_all_attr", "abyss_attr_count_ge", "abyss_distinct_attr_ge",
    "abyss_count_ge", "abyss_empty",
    "prev_char_attr", "own_cost_ge", "own_cost_le",
    "swapped_from_song", "swapped_any", "battle_song",
    "opp_has_area", "hand_attr_count_ge", "own_battle_played",
    "deck_ge", "hand_count_ge", "hand_distinct_attr_ge",
})

CATALOG: dict[str, EffectIR] = {}

_dispatchable: set[int] = set()
_cost_reducing: set[int] = set()

for entry in ENTRIES:
    if entry.effect_id in CATALOG:
        raise ValueError(f"duplicate catalog entry {entry.effect_id}")
    if entry.effect_id not in EFFECT_TO_INDEX:
        raise ValueError(f"catalog entry {entry.effect_id} not in card DB")
    validate_ir(entry, OP_NAMES, _COND_NAMES)
    if entry.custom is not None and entry.custom not in CUSTOM_HANDLERS:
        raise ValueError(f"{entry.effect_id}: unknown custom handler {entry.custom!r}")
    CATALOG[entry.effect_id] = entry
    effect_index = EFFECT_TO_INDEX[entry.effect_id]
    if not entry.inline:
        _dispatchable.add(effect_index)
        EFFECT_PROGRAMS[effect_index] = (entry.cond, entry.ops, entry.custom)

missing = [eid for eid in EFFECT_IDS if eid not in CATALOG]
if missing:
    raise ValueError(f"catalog missing {len(missing)} effects: {missing[:10]}...")

# Old engine: _COST_REDUCING_EFFECTS = {"02-006", "04-065"}
for effect_id in ("02-006", "04-065"):
    _cost_reducing.add(EFFECT_TO_INDEX[effect_id])

DISPATCHABLE_EFFECTS = frozenset(_dispatchable)
COST_REDUCING_EFFECTS = frozenset(_cost_reducing)
