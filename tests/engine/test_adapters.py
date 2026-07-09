"""Unit tests for the decision adapters."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from zutomayo.engine.adapters.bot_agent_adapter import BotAgentDecisionAdapter  # noqa: E402
from zutomayo.engine.adapters.discord_adapter import DiscordDecisionAdapter  # noqa: E402
from zutomayo.engine.decision_broker import DecisionBroker  # noqa: E402
from zutomayo.engine.decisions import (  # noqa: E402
    KIND_CARD_SELECT,
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    KIND_EFFECT_TEXT_INPUT,
    KIND_REDRAW,
    KIND_TCG_SWITCH,
    KIND_TWO_STEP_CARD_SELECT,
    PAYLOAD_INDICES,
    PURPOSE_EFFECT_ORDER,
    PURPOSE_INITIAL_BATTLE_CARD,
    PURPOSE_SET_CARDS,
    DecisionRequest,
    build_card_options,
)
from zutomayo.engine.game_session import GameSession  # noqa: E402
from zutomayo.models.card_instance import CardInstance  # noqa: E402

from tests.scripted_agents import StatelessScriptedAgent  # noqa: E402
from tests.support.game_state_builder import card_by_identity  # noqa: E402


def _instances(identities: list[str]) -> list[CardInstance]:
    return [CardInstance(card=card_by_identity(identity)) for identity in identities]


def _session_with_bot_adapter(agent=None) -> GameSession:
    session = GameSession(game_id='adapter-test', channel_id=1, creator_id=111)
    session.add_player(0)
    adapter = BotAgentDecisionAdapter(agent or StatelessScriptedAgent())
    session.broker = DecisionBroker(session, {0: adapter, 1: adapter})
    return session


class TestBotAgentDecisionAdapter:
    def _request(self, session, **kwargs) -> object:
        request = DecisionRequest(**kwargs)
        return asyncio.run(session.broker.request(request))

    def test_redraw_and_set_cards_routing(self):
        session = _session_with_bot_adapter()
        hand = _instances(['01-001', '01-002', '01-003', '01-004', '01-009', '01-010'])

        response = self._request(
            session, kind=KIND_REDRAW, player_index=1, prompt_text='redraw',
            options=build_card_options(hand), live_objects=hand,
        )
        assert response.payload_type == PAYLOAD_INDICES
        assert response.payload == [0, 1], 'StatelessScriptedAgent redraws len % 4 of a six-card hand'

        response = self._request(
            session, kind=KIND_CARD_SELECT, player_index=1, prompt_text='battle card',
            options=build_card_options(hand), live_objects=hand,
            purpose=PURPOSE_INITIAL_BATTLE_CARD,
        )
        assert response.payload == [5], 'initial battle card routes to choose_initial_battle_card'

        response = self._request(
            session, kind=KIND_CARD_SELECT, player_index=1, prompt_text='set',
            options=build_card_options(hand), live_objects=hand,
            purpose=PURPOSE_SET_CARDS, maximum_selections=1,
        )
        assert response.payload == [0], 'set-cards routes to choose_cards_to_set'

        response = self._request(
            session, kind=KIND_TWO_STEP_CARD_SELECT, player_index=1, prompt_text='set two',
            options=build_card_options(hand), live_objects=hand,
        )
        assert response.payload == [0], 'choose_cards_to_set(6 cards, 2) keeps 1 + (6 % 2) = 1'

    def test_effect_prompts_routing(self):
        session = _session_with_bot_adapter()
        cards = _instances(['01-001', '01-002', '01-003'])

        response = self._request(
            session, kind=KIND_EFFECT_CARD_SELECT, player_index=1, prompt_text='pick',
            options=build_card_options(cards), live_objects=cards,
        )
        assert response.payload == [1], 'stateless agent picks the middle card'

        response = self._request(
            session, kind=KIND_EFFECT_NUMBER_SELECT, player_index=1, prompt_text='number',
            minimum_value=0, maximum_value=4,
        )
        assert response.payload == 2

        response = self._request(
            session, kind=KIND_EFFECT_TEXT_INPUT, player_index=1, prompt_text='type',
        )
        assert response.payload is None, 'agents never answer text prompts'

    def test_effect_order_uses_one_permutation_across_picks(self):
        session = _session_with_bot_adapter()
        cards = _instances(['01-001', '01-002', '01-003'])

        # StatelessScriptedAgent.choose_effect_order reverses the list, so the
        # repeated single picks must come back in reversed order.
        picked = []
        remaining = list(cards)
        while len(remaining) > 1:
            response = self._request(
                session, kind=KIND_EFFECT_CARD_SELECT, player_index=1, prompt_text='order',
                options=build_card_options(remaining), live_objects=remaining,
                purpose=PURPOSE_EFFECT_ORDER,
            )
            chosen = remaining[response.payload[0]]
            picked.append(chosen)
            remaining.remove(chosen)
        picked.extend(remaining)
        assert picked == list(reversed(cards))

    def test_unknown_kind_raises(self):
        adapter = BotAgentDecisionAdapter(StatelessScriptedAgent())
        request = DecisionRequest(kind='mystery', player_index=1, prompt_text='?')
        with pytest.raises(ValueError):
            asyncio.run(adapter._decide(request))


class TestDiscordDecisionAdapter:
    def _view_for(self, kind: str, **request_kwargs):
        session = GameSession(game_id='adapter-test', channel_id=1, creator_id=111)
        session.add_player(222)
        submissions = []
        session.broker = SimpleNamespace(
            submit=lambda sequence_number, payload_type, payload: submissions.append(
                (sequence_number, payload_type, payload)
            ),
        )
        adapter = DiscordDecisionAdapter(transport=SimpleNamespace())
        request = DecisionRequest(kind=kind, player_index=0, prompt_text='prompt', **request_kwargs)
        request.sequence_number = 7
        view = adapter._build_view(session, request)
        return view, submissions

    def test_builds_the_right_view_per_kind(self):
        from zutomayo.ui.tcg_switch_views import SwitchCardsView
        from zutomayo.ui.views import (
            CardSelectView,
            EffectCardSelectView,
            EffectNumberSelectView,
            EffectTextInputView,
            RedrawView,
            TwoStepCardSelectView,
        )

        cards = _instances(['01-001', '01-002'])
        view, _ = self._view_for(KIND_EFFECT_CARD_SELECT, live_objects=cards)
        assert isinstance(view, EffectCardSelectView)

        view, _ = self._view_for(KIND_EFFECT_NUMBER_SELECT, minimum_value=0, maximum_value=3)
        assert isinstance(view, EffectNumberSelectView)

        view, _ = self._view_for(KIND_EFFECT_TEXT_INPUT)
        assert isinstance(view, EffectTextInputView)

        view, _ = self._view_for(KIND_REDRAW, live_objects=cards)
        assert isinstance(view, RedrawView)

        view, _ = self._view_for(KIND_CARD_SELECT, live_objects=cards)
        assert isinstance(view, CardSelectView)

        view, _ = self._view_for(KIND_TWO_STEP_CARD_SELECT, live_objects=cards)
        assert isinstance(view, TwoStepCardSelectView)

        view, _ = self._view_for(
            KIND_TCG_SWITCH,
            live_objects={'main_deck': [card_by_identity('01-001')], 'side_deck': [card_by_identity('01-002')]},
        )
        assert isinstance(view, SwitchCardsView)

    def test_view_submissions_reach_the_broker(self):
        cards = _instances(['01-001', '01-002'])
        view, submissions = self._view_for(KIND_EFFECT_CARD_SELECT, live_objects=cards)
        view.submit_callback(PAYLOAD_INDICES, [1])
        assert submissions == [(7, PAYLOAD_INDICES, [1])]

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            self._view_for('mystery')