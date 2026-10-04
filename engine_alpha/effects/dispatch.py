"""Effect dispatch for the phase driver: start_effect pushes an interpreter frame
when the effect's condition holds.
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
