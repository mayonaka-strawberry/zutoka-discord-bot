"""
Match decision requests: one pending engine decision (or a bot-layer one, such as
the TCG side-deck switch) plus what the presentation layer needs to show it. Engine
answers are single ints, so the decision log is ints except for side-deck switches.

Keep this module import-light: no discord, views, or engine imports beyond
engine_alpha.actions constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from engine_alpha.actions import (
    BINARY,
    SELECT_CARD,
    SELECT_IDENTITY,
    SELECT_NUMBER,
    PURPOSE_NAMES,
)

# Presentation kinds: how a request is shown, distinct from engine int kinds.
KIND_CARD_CHOICE = 'card_choice'
KIND_CARD_MULTI_CHOICE = 'card_multi_choice'      # compound: mulligan, set slots
KIND_IDENTITY_INPUT = 'identity_input'            # name guess text modal
KIND_NUMBER_CHOICE = 'number_choice'
KIND_BINARY_CHOICE = 'binary_choice'
KIND_SIDE_DECK_SWITCH = 'side_deck_switch'        # bot-layer, TCG between matches
KIND_SIDE_CHOICE = 'side_choice'                  # bot-layer, TCG: loser picks day/night

# Response payload types stored in the decision log.
PAYLOAD_ACTION = 'action'          # engine decisions: a single int
PAYLOAD_CARD_KEYS = 'card_keys'    # side-deck switch: {'removed': [...], 'added': [...]}

# KIND_SIDE_CHOICE actions in option order: the timeout fallback takes the first, DAY.
SIDE_ACTION_DAY = 0
SIDE_ACTION_NIGHT = 1
SIDE_LABEL_DAY = 'Day (昼)'
SIDE_LABEL_NIGHT = 'Night (夜)'

ENGINE_PURPOSE_NONE = -1


@dataclass(frozen=True)
class MatchDecisionOption:
    """One selectable option, identified by the action it submits."""
    label: str
    description: str
    action: int


@dataclass
class MatchDecisionRequest:
    kind: str
    player_index: int
    prompt_text: str
    engine_request: Any = None                 # engine_alpha DecisionRequest; None for bot-layer kinds
    purpose: int = ENGINE_PURPOSE_NONE         # engine purpose tag
    options: list[MatchDecisionOption] = field(default_factory=list)
    minimum_value: int = 0                     # number selection bounds (inclusive)
    maximum_value: int = 0
    allow_pass: bool = False
    pass_label: str = ''
    binary_labels: tuple[str, str] = ('No', 'Yes')
    validator: Any = None                      # identity input: text -> action int or None
    timeout_seconds: float = 300.0
    opponent_name: str = 'opponent'
    display_embed: Any = None                  # never serialized
    live_objects: Any = None                   # CardView list / side-deck dict; never serialized
    # Assigned by the broker at request() entry, in deterministic code order.
    sequence_number: int = -1

    def action_count(self) -> int:
        if self.engine_request is not None:
            return len(self.engine_request.legal_actions())
        return len(self.options)


def request_fingerprint(request: MatchDecisionRequest) -> dict[str, Any]:
    """What must match between a logged and a replayed request. Coarse on purpose:
    it catches a changed decision sequence and ignores display text."""
    return {
        'kind': request.kind,
        'purpose': request.purpose,
        'player_index': request.player_index,
        'action_count': request.action_count(),
    }


@dataclass
class MatchDecisionResponse:
    sequence_number: int
    payload_type: str          # PAYLOAD_ACTION | PAYLOAD_CARD_KEYS
    payload: Any               # int action, or the side-deck card-keys dict
    timed_out: bool = False


def engine_kind_to_presentation_kind(engine_kind: int) -> str:
    if engine_kind == SELECT_CARD:
        return KIND_CARD_CHOICE
    if engine_kind == SELECT_IDENTITY:
        return KIND_IDENTITY_INPUT
    if engine_kind == SELECT_NUMBER:
        return KIND_NUMBER_CHOICE
    if engine_kind == BINARY:
        return KIND_BINARY_CHOICE
    raise ValueError(f'unknown engine decision kind {engine_kind!r}')


def purpose_name(purpose: int) -> str:
    if 0 <= purpose < len(PURPOSE_NAMES):
        return PURPOSE_NAMES[purpose]
    return 'NONE'
