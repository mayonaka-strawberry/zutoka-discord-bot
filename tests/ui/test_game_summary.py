"""Overview rendering for /zutomayo summary, focused on how the solo opponent
is named. Players know the models as A and B; the stack behind each letter must
not surface in a summary embed."""

from __future__ import annotations

import re

from zutomayo.ui.game_summary_view import _overview_lines

PLAYER_NAMES = {0: 'Alpha', 1: 'メカうにぐり'}

# Model stack names that must never reach a player-facing surface.
STACK_NAMES = ('alphazero', 'alpha zero', 'ppo', 'transformer')


def _solo_row(solo_difficulty: str) -> dict:
    return {
        'game_id': 'abc123',
        'status': 'completed',
        'is_solo': True,
        'solo_difficulty': solo_difficulty,
    }


def _mode_line(game_row: dict) -> str:
    lines = _overview_lines(game_row, PLAYER_NAMES)
    return next(line for line in lines if line.startswith('**Mode:**'))


def test_solo_overview_names_the_model_by_letter():
    assert _mode_line(_solo_row('alphazero')) == '**Mode:** solo (A)'
    assert _mode_line(_solo_row('ppo')) == '**Mode:** solo (B)'


def test_solo_overview_hides_the_stack_names():
    for opponent in ('alphazero', 'ppo'):
        rendered = ' '.join(_overview_lines(_solo_row(opponent), PLAYER_NAMES))
        for stack_name in STACK_NAMES:
            # Whole words only: "opponent" legitimately contains "ppo".
            assert not re.search(rf'\b{stack_name}\b', rendered, re.IGNORECASE)


def test_legacy_difficulty_rows_still_read_sensibly():
    """Solo games recorded before the model switch stored a difficulty rather
    than a model identifier; those pass through unchanged."""
    assert _mode_line(_solo_row('normal')) == '**Mode:** solo (normal)'
    assert _mode_line(_solo_row('easy')) == '**Mode:** solo (easy)'


def test_non_solo_modes_are_unaffected():
    assert _mode_line({
        'game_id': 'abc123', 'status': 'completed', 'is_tcg': True, 'best_of': 3,
    }) == '**Mode:** TCG best of 3'
    assert _mode_line({'game_id': 'abc123', 'status': 'completed'}) == '**Mode:** standard'
