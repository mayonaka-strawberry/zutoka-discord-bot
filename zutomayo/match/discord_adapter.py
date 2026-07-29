"""
DiscordMatchDecisionAdapter: presents MatchDecisionRequests as Discord DM
views and answers the engine's iterative decisions from compound selections.

Two prompt families are compound: the mulligan (the engine asks card-by-card,
the player answers once with a set of cards to redraw) and set-cards (the
engine asks slot A then slot B, the player answers once with an ordered pick
of up to two cards). For those, the full legacy view (RedrawView /
TwoStepCardSelectView) is presented once and a PendingSelection cache feeds
the engine's individual requests. Both players' compound views go out
concurrently, exactly like the pre-port flows: the second mover's view is
pre-presented while the engine still waits on the first, which is safe
because each player's candidates are their own hand.

If a cached selection ever fails to match the engine's live candidates, the
cache is dropped and the prompt is re-presented sequentially.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from engine_alpha.actions import P_MULLIGAN, P_SET_SLOT_A, P_SET_SLOT_B
from zutomayo.match.decisions import (
    KIND_BINARY_CHOICE,
    KIND_CARD_CHOICE,
    KIND_CARD_MULTI_CHOICE,
    KIND_IDENTITY_INPUT,
    KIND_NUMBER_CHOICE,
    KIND_SIDE_CHOICE,
    KIND_SIDE_DECK_SWITCH,
    PAYLOAD_ACTION,
    MatchDecisionRequest,
)
from zutomayo.match.presentation import maximum_cards_to_set
from zutomayo.match.state_view import card_view

log = logging.getLogger(__name__)

FAMILY_MULLIGAN = 'mulligan'
FAMILY_SET_CARDS = 'set_cards'


class PendingSelection:
    """One player's answer to a compound prompt: the chosen card instance ids
    in pick order (empty list = pass/keep), resolvable before or after the
    engine asks."""

    def __init__(self) -> None:
        self.chosen: Optional[list[int]] = None
        self.consumed_count = 0
        self.on_answer: Optional[Callable[[], None]] = None

    def resolve(self, chosen_instance_ids: list[int]) -> None:
        if self.chosen is not None:
            return
        self.chosen = chosen_instance_ids
        callback, self.on_answer = self.on_answer, None
        if callback is not None:
            callback()


class DiscordMatchDecisionAdapter:
    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.pending_selections: dict[tuple[int, str], PendingSelection] = {}
        self.presented: set[tuple[int, str]] = set()

    def on_phase_changed(self, new_phase: int) -> None:
        self.pending_selections.clear()
        self.presented.clear()

    async def present_decision(self, session: Any, request: MatchDecisionRequest) -> None:
        if request.purpose == P_MULLIGAN:
            await self._handle_compound(session, request, FAMILY_MULLIGAN)
            return
        if request.purpose in (P_SET_SLOT_A, P_SET_SLOT_B):
            await self._handle_compound(session, request, FAMILY_SET_CARDS)
            return
        view = self._build_simple_view(session, request)
        await self.transport.send_to_player(
            session, request.player_index,
            content=request.prompt_text,
            view=view,
        )

    # -- compound families -------------------------------------------------

    async def _handle_compound(self, session: Any, request: MatchDecisionRequest, family: str) -> None:
        player_index = request.player_index
        key = (player_index, family)
        selection = self.pending_selections.get(key)
        if selection is None:
            selection = PendingSelection()
            self.pending_selections[key] = selection
            await self._present_compound_view(session, player_index, family)
            await self._pre_present_other_player(session, 1 - player_index, family)

        def answer_now() -> None:
            action = self._compound_action(selection, request)
            if action is None:
                log.warning(
                    'Compound %s cache for player %d does not match live candidates; re-presenting',
                    family, player_index,
                )
                self.pending_selections.pop(key, None)
                self.presented.discard(key)
                import asyncio

                asyncio.get_running_loop().create_task(
                    self._re_present_sequentially(session, request, family))
                return
            session.broker.submit(request.sequence_number, PAYLOAD_ACTION, action)

        if selection.chosen is not None:
            answer_now()
        else:
            selection.on_answer = answer_now

    async def _re_present_sequentially(self, session: Any, request: MatchDecisionRequest, family: str) -> None:
        key = (request.player_index, family)
        selection = PendingSelection()
        self.pending_selections[key] = selection
        await self._present_compound_view(session, request.player_index, family)
        selection.on_answer = lambda: session.broker.submit(
            request.sequence_number, PAYLOAD_ACTION,
            self._compound_action(selection, request) or 0,
        )

    def _compound_action(self, selection: PendingSelection, request: MatchDecisionRequest) -> Optional[int]:
        """Translate the cached pick list into the action for one engine
        request, or None when the cache does not fit the live candidates."""
        engine_request = request.engine_request
        candidates = list(engine_request.candidates)
        chosen = selection.chosen or []

        if request.purpose == P_MULLIGAN:
            while selection.consumed_count < len(chosen):
                next_instance = chosen[selection.consumed_count]
                selection.consumed_count += 1
                if next_instance in candidates:
                    return candidates.index(next_instance)
                return None
            return len(candidates)  # PASS: all marked

        if request.purpose == P_SET_SLOT_A:
            if not chosen:
                return len(candidates)  # PASS: set nothing
            if chosen[0] in candidates:
                selection.consumed_count = 1
                return candidates.index(chosen[0])
            return None

        # P_SET_SLOT_B
        if len(chosen) < 2:
            return len(candidates)  # PASS: no second card
        if chosen[1] in candidates:
            return candidates.index(chosen[1])
        return None

    async def _pre_present_other_player(self, session: Any, other_index: int, family: str) -> None:
        if (other_index, family) in self.presented:
            return
        if not self.transport.delivers_to_player(session, other_index):
            return
        if family == FAMILY_SET_CARDS:
            state = session.game.state
            player = state.players[other_index]
            if min(maximum_cards_to_set(state, other_index), len(player.hand)) == 0:
                return
        selection = PendingSelection()
        self.pending_selections[(other_index, family)] = selection
        await self._present_compound_view(session, other_index, family)

    async def _present_compound_view(self, session: Any, player_index: int, family: str) -> None:
        from zutomayo.ui.views import ActionSelectView, RedrawView, TwoStepCardSelectView

        key = (player_index, family)
        self.presented.add(key)
        selection = self.pending_selections[key]
        state = session.game.state
        hand_views = [card_view(state, instance_id) for instance_id in state.players[player_index].hand]
        instance_ids = [view.instance_id for view in hand_views]

        if family == FAMILY_MULLIGAN:
            def redraw_callback(payload_type: str, payload: Any) -> None:
                selection.resolve([instance_ids[i] for i in payload])

            view = RedrawView(
                session, player_index, hand_views,
                opponent_name=self._opponent_name(session, player_index),
                submit_callback=redraw_callback,
            )
            prompt = 'Select the cards you want to redraw [最初の手札を引いたとき、一度だけ引き直しができます。]'
            await self.transport.send_to_player(session, player_index, content=prompt, view=view)
            return

        maximum = min(maximum_cards_to_set(state, player_index), len(instance_ids))
        if maximum >= 2:
            def two_step_callback(payload_type: str, payload: Any) -> None:
                selection.resolve([instance_ids[i] for i in payload])

            view = TwoStepCardSelectView(
                session, player_index, hand_views,
                opponent_name=self._opponent_name(session, player_index),
                submit_callback=two_step_callback,
            )
            prompt = 'You may set up to 2 cards. Select your first card.'
        else:
            def single_callback(payload_type: str, payload: Any) -> None:
                selection.resolve([] if payload >= len(instance_ids) else [instance_ids[payload]])

            view = ActionSelectView(
                session, player_index, hand_views,
                placeholder='Select a card to set...',
                allow_pass=True,
                pass_label='Set nothing',
                confirm=True,
                opponent_name=self._opponent_name(session, player_index),
                submit_callback=single_callback,
            )
            prompt = 'Set a card from your hand.'
        await self.transport.send_to_player(session, player_index, content=prompt, view=view)

    # -- simple kinds --------------------------------------------------------

    def _build_simple_view(self, session: Any, request: MatchDecisionRequest) -> Any:
        from zutomayo.ui.views import (
            ActionSelectView,
            BinaryChoiceView,
            ConfirmableChoiceView,
            EffectNumberSelectView,
            EffectTextInputView,
        )

        sequence_number = request.sequence_number

        def submit_action(payload_type: str, payload: Any) -> None:
            session.broker.submit(sequence_number, PAYLOAD_ACTION, payload)

        if request.kind == KIND_CARD_CHOICE:
            from engine_alpha.actions import P_INITIAL_CARD

            return ActionSelectView(
                session, request.player_index, request.live_objects,
                allow_pass=request.allow_pass,
                pass_label=request.pass_label,
                confirm=(request.purpose == P_INITIAL_CARD),
                opponent_name=request.opponent_name,
                submit_callback=submit_action,
            )
        if request.kind == KIND_NUMBER_CHOICE:
            def submit_number(payload_type: str, payload: Any) -> None:
                session.broker.submit(sequence_number, PAYLOAD_ACTION, int(payload))

            return EffectNumberSelectView(
                session, request.player_index,
                request.minimum_value, request.maximum_value,
                submit_callback=submit_number,
            )
        if request.kind == KIND_IDENTITY_INPUT:
            def modal_validator(text: str) -> Optional[str]:
                if request.validator(text) is None:
                    return 'Unknown or invalid card. Enter a card ID like 03-045.'
                return None

            def submit_text(payload_type: str, payload: Any) -> None:
                action = request.validator(payload)
                if action is not None:
                    session.broker.submit(sequence_number, PAYLOAD_ACTION, action)

            return EffectTextInputView(
                session, request.player_index,
                validator=modal_validator,
                prompt_text=request.prompt_text,
                submit_callback=submit_text,
            )
        if request.kind == KIND_BINARY_CHOICE:
            return BinaryChoiceView(
                session, request.player_index,
                labels=request.binary_labels,
                submit_callback=submit_action,
            )
        if request.kind == KIND_SIDE_CHOICE:
            return ConfirmableChoiceView(
                session, request.player_index,
                options=request.options,
                prompt_text=request.prompt_text,
                opponent_name=request.opponent_name,
                submit_callback=submit_action,
            )
        if request.kind == KIND_SIDE_DECK_SWITCH:
            from zutomayo.ui.tcg_switch_views import SwitchCardsView

            def submit_card_keys(payload_type: str, payload: Any) -> None:
                session.broker.submit(sequence_number, payload_type, payload)

            return SwitchCardsView(
                session=session,
                player_index=request.player_index,
                main_deck=request.live_objects['main_deck'],
                side_deck=request.live_objects['side_deck'],
                opponent_name=request.opponent_name,
                submit_callback=submit_card_keys,
            )
        raise ValueError(f'DiscordMatchDecisionAdapter cannot present kind {request.kind!r}')

    def _opponent_name(self, session: Any, player_index: int) -> str:
        name = self.transport.display_name(session, 1 - player_index)
        return name or 'opponent'
