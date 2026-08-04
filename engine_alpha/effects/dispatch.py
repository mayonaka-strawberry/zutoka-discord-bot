"""Effect dispatch surface used by the phase driver.

Backed by the IR catalog: HANDLED_EFFECTS mirrors the old engine's handler
registry (every effect id except the engine-inline passives), COST_REDUCING
mirrors _COST_REDUCING_EFFECTS, and start_effect delegates to the IR
interpreter, which pushes a Frame when the effect's gate condition holds.
"""

from __future__ import annotations

from ..cards import EFFECT_T
from ..state import GameState
from .catalog import COST_REDUCING_EFFECTS, DISPATCHABLE_EFFECTS
from . import interpreter

HANDLED_EFFECTS: frozenset[int] = DISPATCHABLE_EFFECTS
COST_REDUCING: frozenset[int] = COST_REDUCING_EFFECTS


def start_effect(state: GameState, owner_index: int, instance_id: int) -> None:
    interpreter.start_effect(state, owner_index, instance_id,
                             EFFECT_T[state.inst_def[instance_id]])
