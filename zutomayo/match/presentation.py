"""
Builds MatchDecisionRequests from pending engine decisions: presentation
kind, prompt text (with the resolving effect's card as context), selectable
options with their engine actions, and the CardView list the Discord views
render. Pure data - no discord imports.
"""

from __future__ import annotations

from typing import Any, Optional

from engine_alpha.actions import (
    BINARY,
    SELECT_CARD,
    SELECT_IDENTITY,
    SELECT_NUMBER,
    P_CHRONOS_VALUE,
    P_EFFECT_NUMBER,
    P_EFFECT_ORDER,
    P_EFFECT_TARGET,
    P_INITIAL_CARD,
    P_MULLIGAN,
    P_NAME_GUESS,
    P_SET_SLOT_A,
    P_SET_SLOT_B,
    P_SKIP_SWAP,
)
from engine_alpha.state import PH_PROCESS_EFFECTS
from zutomayo.match.decisions import (
    KIND_BINARY_CHOICE,
    KIND_CARD_CHOICE,
    KIND_CARD_MULTI_CHOICE,
    KIND_IDENTITY_INPUT,
    KIND_NUMBER_CHOICE,
    MatchDecisionOption,
    MatchDecisionRequest,
)
from zutomayo.match.state_view import card_view, definition_index_to_card

EFFECT_PROMPT_TIMEOUT_SECONDS = 300.0
FLOW_PROMPT_TIMEOUT_SECONDS = 300.0

PASS_LABEL_BY_PURPOSE = {
    P_MULLIGAN: 'Keep hand',
    P_SET_SLOT_A: 'Set nothing',
    P_SET_SLOT_B: 'Set no second card',
    P_EFFECT_TARGET: 'Skip',
}

SKIP_SWAP_LABELS = ('Swap in the new character', 'Keep current battle character')


def option_label(card: Any) -> tuple[str, str]:
    return f'{card.pack:02d}-{card.id:03d}', card.name


def effect_source_card(state) -> Optional[Any]:
    """The card whose effect is asking, when a decision arises mid-effect."""
    if state.frame_stack:
        frame = state.frame_stack[-1]
        return definition_index_to_card(state.inst_def[frame.source])
    return None


def _effect_prefix(state) -> str:
    card = effect_source_card(state)
    if card is None:
        return ''
    return f'**{card.name}** [{card.name_jp}] effect: '


def _effect_order_prompt(state, engine_request) -> str:
    """Numbered the way the pre-port engine numbered it: the step counts every
    effect already placed in the resolution order, including the cost-reducing
    ones the rules force to the front."""
    card_names = ', '.join(
        definition_index_to_card(state.inst_def[instance_id]).name
        for instance_id in engine_request.candidates
    )
    ordered_count = 0
    if state.phase == PH_PROCESS_EFFECTS and len(state.phase_ctx) == 7:
        ordered_count = len(state.phase_ctx[4])
    return (
        f'**Choose effect order ({ordered_count + 1}/'
        f'{ordered_count + len(engine_request.candidates)})** '
        f'[効果の処理順を選んでください]\n'
        f'Remaining effects: {card_names}\n'
        f'Select which effect to resolve next:'
    )


def _prompt_for(state, engine_request) -> str:
    purpose = engine_request.purpose
    if purpose == P_MULLIGAN:
        return 'Select the cards you want to redraw [最初の手札を引いたとき、一度だけ引き直しができます。]'
    if purpose == P_INITIAL_CARD:
        return ('Choose one card to place face-down in the Battle Zone '
                '[手札からカードを１枚選びバトルゾーンに裏向きにして置きます。]')
    if purpose in (P_SET_SLOT_A, P_SET_SLOT_B):
        return 'Set cards from your hand.'
    if purpose == P_EFFECT_ORDER:
        return _effect_order_prompt(state, engine_request)
    if purpose == P_EFFECT_TARGET:
        return f'{_effect_prefix(state)}select a card.'
    if purpose == P_EFFECT_NUMBER:
        return f'{_effect_prefix(state)}select a number.'
    if purpose == P_CHRONOS_VALUE:
        return f'{_effect_prefix(state)}select the chronos position.'
    if purpose == P_NAME_GUESS:
        return f'{_effect_prefix(state)}name a card.'
    if purpose == P_SKIP_SWAP:
        return 'A character is waiting to swap in. Keep your current battle character or swap?'
    return 'Make your choice.'


def _identity_validator(engine_request):
    """Maps typed text to an engine action (a card definition index) or None.
    Accepts the card key (for example 03-045) or an exact card name."""
    from engine_alpha.cards import KEY_TO_INDEX

    legal = set(engine_request.legal)

    def validate(text: str) -> Optional[int]:
        cleaned = text.strip()
        definition_index = KEY_TO_INDEX.get(cleaned)
        if definition_index is None and '-' in cleaned:
            pack_text, _, number_text = cleaned.partition('-')
            if pack_text.strip().isdigit() and number_text.strip().isdigit():
                key = f'{int(pack_text):02d}-{int(number_text):03d}'
                definition_index = KEY_TO_INDEX.get(key)
        if definition_index is None:
            lowered = cleaned.lower()
            for candidate in legal:
                card = definition_index_to_card(candidate)
                if lowered in (card.name.lower(), card.name_jp.lower()):
                    definition_index = candidate
                    break
        if definition_index is not None and definition_index in legal:
            return definition_index
        return None

    return validate


def build_match_request(game, engine_request, opponent_name: str = 'opponent') -> MatchDecisionRequest:
    state = game.state
    player_index = state.acting
    purpose = engine_request.purpose

    if engine_request.kind == SELECT_CARD:
        views = [card_view(state, instance_id) for instance_id in engine_request.candidates]
        options = []
        for action, view in enumerate(views):
            label, description = option_label(view.card)
            options.append(MatchDecisionOption(label=label, description=description, action=action))
        kind = KIND_CARD_MULTI_CHOICE if purpose in (P_MULLIGAN, P_SET_SLOT_A, P_SET_SLOT_B) else KIND_CARD_CHOICE
        return MatchDecisionRequest(
            kind=kind,
            player_index=player_index,
            prompt_text=_prompt_for(state, engine_request),
            engine_request=engine_request,
            purpose=purpose,
            options=options,
            allow_pass=engine_request.allow_pass,
            pass_label=PASS_LABEL_BY_PURPOSE.get(purpose, 'Pass'),
            timeout_seconds=FLOW_PROMPT_TIMEOUT_SECONDS,
            opponent_name=opponent_name,
            live_objects=views,
        )

    if engine_request.kind == SELECT_IDENTITY:
        return MatchDecisionRequest(
            kind=KIND_IDENTITY_INPUT,
            player_index=player_index,
            prompt_text=_prompt_for(state, engine_request),
            engine_request=engine_request,
            purpose=purpose,
            validator=_identity_validator(engine_request),
            timeout_seconds=EFFECT_PROMPT_TIMEOUT_SECONDS,
            opponent_name=opponent_name,
        )

    if engine_request.kind == SELECT_NUMBER:
        return MatchDecisionRequest(
            kind=KIND_NUMBER_CHOICE,
            player_index=player_index,
            prompt_text=_prompt_for(state, engine_request),
            engine_request=engine_request,
            purpose=purpose,
            minimum_value=engine_request.lo,
            maximum_value=engine_request.hi,
            timeout_seconds=EFFECT_PROMPT_TIMEOUT_SECONDS,
            opponent_name=opponent_name,
        )

    if engine_request.kind == BINARY:
        labels = SKIP_SWAP_LABELS if purpose == P_SKIP_SWAP else ('No', 'Yes')
        return MatchDecisionRequest(
            kind=KIND_BINARY_CHOICE,
            player_index=player_index,
            prompt_text=_prompt_for(state, engine_request),
            engine_request=engine_request,
            purpose=purpose,
            binary_labels=labels,
            timeout_seconds=EFFECT_PROMPT_TIMEOUT_SECONDS,
            opponent_name=opponent_name,
        )

    raise ValueError(f'cannot present engine decision kind {engine_request.kind!r}')


def maximum_cards_to_set(state, player_index: int) -> int:
    """Mirrors the engine's set-slot rule: previous-battle loser may set two."""
    if state.last_battle_winner == -1:
        return 1
    return 1 if state.last_battle_winner == player_index else 2
