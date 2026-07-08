"""
Characterization tests for pack 01 card effects.

These tests assert CURRENT behavior (verified identical to the pre-refactor
engine by the transcript baselines); they are not a rules re-derivation.
Do not "fix" behavior here — known intended rulings are documented in the
project notes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import run_effect  # noqa: E402

# Effectless fixture characters, one per attribute:
DARKNESS_CHARACTER = '01-001'
FLAME_CHARACTER = '01-002'
ELECTRICITY_CHARACTER = '01-003'
WIND_CHARACTER = '01-004'
SECOND_DARKNESS_CHARACTER = '01-009'


class TestEffect01007:
    """Attack +50 if the Abyss's cards have four different attributes."""

    def test_four_attributes_grant_attack_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-007')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
                 .build())
        result = run_effect(state, '01-007', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 50
        assert result.engine.turn_state.attack_bonus[1] == 0
        assert result.prompts_seen == []

    def test_duplicate_attributes_do_not_count_twice(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-007')
                 .with_abyss(0, [DARKNESS_CHARACTER, SECOND_DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER])
                 .build())
        result = run_effect(state, '01-007', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 0

    def test_empty_abyss_grants_nothing(self):
        state = GameStateBuilder().with_battle_card(0, '01-007').build()
        result = run_effect(state, '01-007', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 0
