"""
ModelDecisionAdapter: lets a trained model answer broker decisions in solo
games. The agent computes an engine action off the event loop (bounded by a
watchdog timeout); on any failure a legal fallback action is submitted so a
model bug can never hang a game.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from zutomayo.match.broker import fallback_response_payload
from zutomayo.match.decisions import PAYLOAD_ACTION, MatchDecisionRequest

log = logging.getLogger(__name__)

AGENT_TIMEOUT_SECONDS = 45.0


def create_solo_agent(opponent: str) -> Any:
    """The inference agent for a solo opponent identifier."""
    if opponent == 'alphazero':
        from alpha_zero.inference import AlphaZeroAgent

        return AlphaZeroAgent()
    if opponent == 'ppo':
        from ppo_transformer.inference import PpoAgent

        return PpoAgent()
    raise ValueError(f'Unknown solo opponent {opponent!r}')


class ModelDecisionAdapter:
    def __init__(self, agent: Any, broker_getter: Callable[[], Any]) -> None:
        self.agent = agent
        self.broker_getter = broker_getter

    async def present_decision(self, session: Any, request: MatchDecisionRequest) -> None:
        game = session.game
        try:
            action = await asyncio.wait_for(
                asyncio.to_thread(self.agent.act, game), AGENT_TIMEOUT_SECONDS)
        except Exception:
            log.exception('Model agent failed for game %s; using the fallback action',
                          session.game_id)
            payload_type, payload = fallback_response_payload(request)
            self.broker_getter().submit(request.sequence_number, payload_type, payload)
            return
        self.broker_getter().submit(request.sequence_number, PAYLOAD_ACTION, action)

    def on_phase_changed(self, new_phase: int) -> None:
        return None
