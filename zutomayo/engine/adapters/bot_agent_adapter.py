"""
BotAgentDecisionAdapter: answers DecisionRequests with a BotAgent (the solo
opponent), replacing both the inline bot branches in SoloGameFlow and the
BotEffectEngine prompt overrides.

Behavior parity notes:
- Model inference runs in a worker thread with the same 45 second guard the
  solo flow used (``asyncio.wait_for(asyncio.to_thread(...), timeout=45.0)``);
  a timeout raises, exactly as before.
- ``ModelBotAgent.current_game_state`` is refreshed before every decision
  (replaces SoloGameFlow._update_bot_game_state).
- Effect-order prompts arrive as repeated single-card selections (that is how
  the engine composes them for humans), but the bot's contract is one
  ``choose_effect_order`` permutation call. Requests carrying
  ``purpose='effect_order'`` are answered from a cached permutation computed
  once per ordering sequence, reproducing the one-shot semantics exactly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from zutomayo.engine.decisions import (
    KIND_CARD_SELECT,
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    KIND_EFFECT_TEXT_INPUT,
    KIND_REDRAW,
    KIND_TWO_STEP_CARD_SELECT,
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TEXT,
    PURPOSE_EFFECT_ORDER,
    PURPOSE_INITIAL_BATTLE_CARD,
    DecisionRequest,
)

if TYPE_CHECKING:
    from zutomayo.engine.bot_agent import BotAgent
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card_instance import CardInstance

log = logging.getLogger(__name__)

BOT_DECISION_TIMEOUT_SECONDS = 45.0


class BotAgentDecisionAdapter:
    def __init__(self, bot_agent: 'BotAgent') -> None:
        self.bot_agent = bot_agent
        # Cached permutation for the effect-order sequence in progress.
        self._effect_order_remaining: Optional[list['CardInstance']] = None

    async def present_decision(self, session: 'GameSession', request: DecisionRequest) -> None:
        self._refresh_bot_game_state(session)
        payload_type, payload = await self._decide(request)
        session.broker.submit(request.sequence_number, payload_type, payload)

    def _refresh_bot_game_state(self, session: 'GameSession') -> None:
        from zutomayo.engine.bot_agent import ModelBotAgent

        if isinstance(self.bot_agent, ModelBotAgent):
            self.bot_agent.current_game_state = session.game_state

    async def _run_agent(self, function: Any, *arguments: Any) -> Any:
        return await asyncio.wait_for(
            asyncio.to_thread(function, *arguments),
            timeout=BOT_DECISION_TIMEOUT_SECONDS,
        )

    async def _decide(self, request: DecisionRequest) -> tuple[str, Any]:
        cards = request.live_objects

        if request.kind == KIND_REDRAW:
            chosen = await self._run_agent(self.bot_agent.choose_redraw, cards[:])
            return PAYLOAD_INDICES, [cards.index(card_instance) for card_instance in chosen]

        if request.kind == KIND_CARD_SELECT:
            if request.purpose == PURPOSE_INITIAL_BATTLE_CARD:
                chosen_card = await self._run_agent(self.bot_agent.choose_initial_battle_card, cards[:])
                return PAYLOAD_INDICES, [cards.index(chosen_card)]
            chosen = await self._run_agent(
                self.bot_agent.choose_cards_to_set, cards[:], request.maximum_selections,
            )
            return PAYLOAD_INDICES, [cards.index(card_instance) for card_instance in chosen]

        if request.kind == KIND_TWO_STEP_CARD_SELECT:
            chosen = await self._run_agent(self.bot_agent.choose_cards_to_set, cards[:], 2)
            return PAYLOAD_INDICES, [cards.index(card_instance) for card_instance in chosen]

        if request.kind == KIND_EFFECT_CARD_SELECT:
            if request.purpose == PURPOSE_EFFECT_ORDER:
                chosen_card = await self._next_effect_order_pick(cards)
            else:
                chosen_card = await self._run_agent(self.bot_agent.choose_effect_card, cards[:])
            if chosen_card is None:
                return PAYLOAD_INDICES, None
            return PAYLOAD_INDICES, [cards.index(chosen_card)]

        if request.kind == KIND_EFFECT_NUMBER_SELECT:
            value = await self._run_agent(
                self.bot_agent.choose_effect_number, request.minimum_value, request.maximum_value,
            )
            return PAYLOAD_NUMBER, value

        if request.kind == KIND_EFFECT_TEXT_INPUT:
            value = await self._run_agent(self.bot_agent.choose_effect_text)
            return PAYLOAD_TEXT, value

        raise ValueError(f'BotAgentDecisionAdapter cannot answer decision kind {request.kind!r}')

    async def _next_effect_order_pick(self, remaining_cards: list['CardInstance']) -> 'CardInstance':
        """
        Answer one pick of an effect-order sequence using a permutation chosen
        once for the whole sequence, matching choose_effect_order semantics.
        """
        cached = self._effect_order_remaining
        if not cached or {id(card) for card in cached} != {id(card) for card in remaining_cards}:
            ordered = await self._run_agent(self.bot_agent.choose_effect_order, list(remaining_cards))
            cached = list(ordered)
        chosen_card = cached[0]
        self._effect_order_remaining = cached[1:] or None
        return chosen_card
