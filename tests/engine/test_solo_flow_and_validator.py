"""Tests for the solo flow's remaining unique pieces and the shared card-id
validator used by the guessing effects."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.effects.cards._card_id_validator import validate_card_id  # noqa: E402
from zutomayo.engine.adapters.bot_agent_adapter import BotAgentDecisionAdapter  # noqa: E402
from zutomayo.engine.adapters.discord_adapter import DiscordDecisionAdapter  # noqa: E402
from zutomayo.engine.game_session import GameSession  # noqa: E402
from zutomayo.engine.solo_game_flow import SoloGameFlow  # noqa: E402

from tests.fake_adapters import RecordingTransport  # noqa: E402
from tests.scripted_agents import StatelessScriptedAgent  # noqa: E402
from tests.transcript import TranscriptRecorder  # noqa: E402


class TestValidateCardId:
    def test_accepts_known_cards(self):
        assert validate_card_id('01-001') is None
        assert validate_card_id('  03-047 ') is None

    def test_rejects_bad_format_and_unknown_cards(self):
        assert 'Invalid format' in validate_card_id('1-1')
        assert 'Invalid format' in validate_card_id('abcdef')
        assert 'does not exist' in validate_card_id('99-999')


class TestSoloGameFlow:
    def _solo_session(self) -> GameSession:
        session = GameSession(game_id='solo-flow-test', channel_id=1, creator_id=111)
        session.add_player(0)
        session.is_solo = True
        return session

    def test_ensure_decision_runtime_wires_both_adapter_kinds(self):
        flow = SoloGameFlow(bot=None, bot_agent=StatelessScriptedAgent())
        session = self._solo_session()
        flow._ensure_decision_runtime(session)
        assert isinstance(session.broker.adapters[0], DiscordDecisionAdapter)
        assert isinstance(session.broker.adapters[1], BotAgentDecisionAdapter)

        # Idempotent: a second call keeps the existing runtime.
        broker = session.broker
        flow._ensure_decision_runtime(session)
        assert session.broker is broker

    def test_deck_building_gives_the_bot_a_deck(self):
        flow = SoloGameFlow(bot=None, bot_agent=StatelessScriptedAgent())
        session = self._solo_session()
        session.transport = RecordingTransport(TranscriptRecorder())
        flow._ensure_decision_runtime(session)

        # The human never answers: the phase clears pending state first, so
        # stub the 750-second wait to return immediately (timeout semantics).
        async def immediate_timeout(timeout: float = 750.0) -> bool:
            return False

        session.wait_for_both_players = immediate_timeout
        human_deck, bot_deck = asyncio.run(flow._do_deck_building_phase(session))
        assert human_deck is None
        assert bot_deck is not None and len(bot_deck) == 20
        assert session.player_deck_names[1] == '<bot>'