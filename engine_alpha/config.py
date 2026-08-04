"""Engine-level constants as a dataclass, for reproducible run records.

The engine itself uses inline constants for speed; this dataclass documents
them and lets training stacks include the engine section in config dumps.
Training hyperparameters live with their stacks (see alpha_zero/config.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EngineConfig:
    deck_size: int = 20
    max_copies: int = 2
    starting_hp: int = 100
    opening_hand_size: int = 5
    chronos_size: int = 18
    midnight: int = 4
    night_end: int = 8
    noon: int = 13
    max_turns: int = 200  # hard safety cap; a legal game always deck-outs long before this
