"""
DiscordDecisionAdapter: presents DecisionRequests as the existing Discord DM
views, byte-identical to the pre-broker behavior (same prompt text, dropdown
options, confirm/reselect flows, and waiting messages).

The views answer through ``submit_callback``, which routes to
``session.broker.submit(sequence_number, payload_type, payload)`` with
JSON-serializable payloads (option indices, a number, or text).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from zutomayo.engine.decisions import (
    KIND_CARD_SELECT,
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    KIND_EFFECT_TEXT_INPUT,
    KIND_REDRAW,
    KIND_TWO_STEP_CARD_SELECT,
    DecisionRequest,
)

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.engine.match_transport import MatchTransport

log = logging.getLogger(__name__)


class DiscordDecisionAdapter:
    def __init__(self, transport: 'MatchTransport') -> None:
        self.transport = transport

    async def present_decision(self, session: 'GameSession', request: DecisionRequest) -> None:
        view = self._build_view(session, request)
        await self.transport.send_to_player(
            session, request.player_index,
            content=request.prompt_text,
            view=view,
        )

    def _build_view(self, session: 'GameSession', request: DecisionRequest) -> Any:
        # Imported here so this module stays importable without discord installed.
        from zutomayo.ui.views import (
            CardSelectView,
            EffectCardSelectView,
            EffectNumberSelectView,
            EffectTextInputView,
            RedrawView,
            TwoStepCardSelectView,
        )

        sequence_number = request.sequence_number

        def submit_callback(payload_type: str, payload: Any) -> None:
            session.broker.submit(sequence_number, payload_type, payload)

        if request.kind == KIND_EFFECT_CARD_SELECT:
            return EffectCardSelectView(
                session, request.player_index, request.live_objects,
                placeholder=request.placeholder,
                submit_callback=submit_callback,
            )
        if request.kind == KIND_EFFECT_NUMBER_SELECT:
            return EffectNumberSelectView(
                session, request.player_index,
                request.minimum_value, request.maximum_value,
                placeholder=request.placeholder,
                label_prefix=request.label_prefix,
                submit_callback=submit_callback,
            )
        if request.kind == KIND_EFFECT_TEXT_INPUT:
            return EffectTextInputView(
                session, request.player_index,
                modal_title=request.modal_title,
                button_label=request.button_label,
                label=request.input_label,
                placeholder=request.input_placeholder,
                validator=request.validator,
                prompt_text=request.prompt_text,
                submit_callback=submit_callback,
            )
        if request.kind == KIND_REDRAW:
            return RedrawView(
                session, request.player_index, request.live_objects,
                opponent_name=request.opponent_name,
                submit_callback=submit_callback,
            )
        if request.kind == KIND_CARD_SELECT:
            return CardSelectView(
                session, request.player_index, request.live_objects,
                min_cards=request.minimum_selections,
                max_cards=request.maximum_selections,
                placeholder=request.placeholder,
                embed=request.display_embed,
                opponent_name=request.opponent_name,
                submit_callback=submit_callback,
            )
        if request.kind == KIND_TWO_STEP_CARD_SELECT:
            return TwoStepCardSelectView(
                session, request.player_index, request.live_objects,
                embed=request.display_embed,
                opponent_name=request.opponent_name,
                submit_callback=submit_callback,
            )
        raise ValueError(f'DiscordDecisionAdapter cannot present decision kind {request.kind!r}')
