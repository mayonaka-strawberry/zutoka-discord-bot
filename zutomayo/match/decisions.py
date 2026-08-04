"""
Decision requests for matches driven by the engine_alpha state machine.

A MatchDecisionRequest wraps one pending engine decision (or one bot-layer
decision such as the TCG side-deck switch) with everything the presentation
layer needs to render it. Responses are always a single int action applied
with ``Game.apply(action)``, so the persisted decision log is a pure int
stream and replay is trivial.

This module must stay import-light: no discord, no views, no engine imports
at module level beyond engine_alpha.actions constants.
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

# KIND_SIDE_CHOICE actions, listed in option order so the broker's
# lowest-action timeout fallback resolves to DAY. The labels are shared by the
# prompt, the announcement and the summary view.
SIDE_ACTION_DAY = 0
SIDE_ACTION_NIGHT = 1
SIDE_LABEL_DAY = 'Day (昼)'
SIDE_LABEL_NIGHT = 'Night (夜)'

ENGINE_PURPOSE_NONE = -1


@dataclass(frozen=True)
class MatchDecisionOption:
    """One selectable option, identified by the engine action it submits."""
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
    """
    The shape of a request that must match between the logged game and a
    replayed game for a log entry to be trusted. Deliberately coarse: it
    detects structural divergence (a different decision sequence after a
    code change) without being brittle about display text.
    """
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
