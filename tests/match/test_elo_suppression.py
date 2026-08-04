"""The turn-1 self-defeat rule: a game the loser threw with a CHAOS bank-or-lose
card pays the winner no Elo.

The point of this file is the boundary. The rule is deliberately scoped to turn 1
and to the standard ladder, so most of what is pinned here is what it must NOT
touch: later turns, TCG matches, draws, and ordinary games.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional

import pytest

from zutomayo.match.match_flow import SingleMatchFlow, should_suppress_winner_elo_gain
from tests.match.support import FakeSession


def state_with(self_defeat_player: int, self_defeat_turn: int) -> SimpleNamespace:
    return SimpleNamespace(
        self_defeat_player=self_defeat_player,
        self_defeat_turn=self_defeat_turn,
    )


# (label, self_defeat_player, self_defeat_turn, mode, winner, expected)
BOUNDARY_CASES = [
    ('turn 1, player 1 threw it, player 0 won', 1, 1, 'standard', 0, True),
    ('turn 1, player 0 threw it, player 1 won', 0, 1, 'standard', 1, True),
    ('turn 2 is outside the rule', 1, 2, 'standard', 0, False),
    ('turn 3 is outside the rule', 1, 3, 'standard', 0, False),
    ('turn 50 is outside the rule', 1, 50, 'standard', 0, False),
    ('ordinary game, no self-defeat', -1, -1, 'standard', 0, False),
    ('self-defeater somehow won the tie-break', 1, 1, 'standard', 1, False),
    ('TCG is out of scope', 1, 1, 'tcg_match', 0, False),
    ('draws are out of scope', 1, 1, 'standard', None, False),
]


@pytest.mark.parametrize(
    'label,self_defeat_player,self_defeat_turn,mode,winner,expected',
    BOUNDARY_CASES,
    ids=[case[0] for case in BOUNDARY_CASES],
)
def test_suppression_boundary(label, self_defeat_player, self_defeat_turn,
                              mode, winner, expected):
    state = state_with(self_defeat_player, self_defeat_turn)
    assert should_suppress_winner_elo_gain(state, mode, winner) is expected, label


class SessionWithDiscordIds(FakeSession):
    def get_discord_id(self, player_index: int) -> Optional[int]:
        for discord_id, index in self.player_discord_ids.items():
            if index == player_index:
                return discord_id
        return None


def record_and_capture(monkeypatch, *, self_defeat_turn: int, winner: int = 0) -> dict:
    """Run _record_match_stats against a stubbed recorder and return its kwargs."""
    captured: dict = {}

    async def fake_record_match_result(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        'zutomayo.data.player_storage.record_match_result', fake_record_match_result,
    )

    session = SessionWithDiscordIds()
    session.game = SimpleNamespace(state=SimpleNamespace(
        winner=winner, self_defeat_player=1, self_defeat_turn=self_defeat_turn,
    ))
    asyncio.run(SingleMatchFlow.__new__(SingleMatchFlow)._record_match_stats(session))
    return captured


def test_turn_one_self_defeat_reaches_the_recorder(monkeypatch):
    captured = record_and_capture(monkeypatch, self_defeat_turn=1)
    assert captured['suppress_winner_elo_gain'] is True


def test_turn_two_self_defeat_rates_normally(monkeypatch):
    captured = record_and_capture(monkeypatch, self_defeat_turn=2)
    assert captured['suppress_winner_elo_gain'] is False


def test_ordinary_game_rates_normally(monkeypatch):
    captured = record_and_capture(monkeypatch, self_defeat_turn=-1)
    assert captured['suppress_winner_elo_gain'] is False
