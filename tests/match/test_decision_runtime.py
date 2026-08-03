"""Which adapter answers for which seat when a flow builds the decision runtime.

run_solo_game installs its own broker up front, so this is the path a *resumed*
solo game takes. Getting it wrong is silent: the bot seat would be prompted by
DM at the sentinel id, transport.send_to_player would drop the message, and the
bot would forfeit on consecutive timeouts rather than play.
"""

from __future__ import annotations

import zutomayo.match.solo_flow as solo_flow_module
from zutomayo.match.discord_adapter import DiscordMatchDecisionAdapter
from zutomayo.match.match_flow import SingleMatchFlow
from zutomayo.match.solo_flow import HUMAN_PLAYER_INDEX, MODEL_PLAYER_INDEX
from tests.match.support import FakeSession

MODEL_ADAPTER_SENTINEL = object()


def build_runtime(*, solo: bool, difficulty: str = 'normal') -> FakeSession:
    session = FakeSession()
    session.is_solo = solo
    session.solo_difficulty = difficulty
    SingleMatchFlow(bot=None)._ensure_decision_runtime(session)
    return session


def stub_model_adapter(monkeypatch) -> list[tuple]:
    """Stand in for create_model_adapter: no checkpoint is deployed in CI, so
    the real call would raise."""
    calls: list[tuple] = []

    def fake_create_model_adapter(session, opponent):
        calls.append((session, opponent))
        return MODEL_ADAPTER_SENTINEL

    monkeypatch.setattr(solo_flow_module, 'create_model_adapter', fake_create_model_adapter)
    return calls


def test_two_player_games_answer_both_seats_over_discord():
    session = build_runtime(solo=False)

    adapters = session.broker.adapters
    assert isinstance(adapters[0], DiscordMatchDecisionAdapter)
    assert adapters[1] is adapters[0]


def test_solo_games_seat_the_model_on_player_one(monkeypatch):
    calls = stub_model_adapter(monkeypatch)
    session = build_runtime(solo=True, difficulty='alphazero')

    adapters = session.broker.adapters
    assert isinstance(adapters[HUMAN_PLAYER_INDEX], DiscordMatchDecisionAdapter)
    assert adapters[MODEL_PLAYER_INDEX] is MODEL_ADAPTER_SENTINEL
    assert calls == [(session, 'alphazero')], 'the saved difficulty picks the opponent'


def test_an_existing_broker_is_left_alone(monkeypatch):
    stub_model_adapter(monkeypatch)
    session = FakeSession()
    session.is_solo = True
    session.broker = 'already built by run_solo_game'

    SingleMatchFlow(bot=None)._ensure_decision_runtime(session)

    assert session.broker == 'already built by run_solo_game'
