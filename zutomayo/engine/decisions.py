"""
Decision request and response types for the decision broker.

A DecisionRequest describes one interactive choice a player must make,
independent of the medium that presents it (Discord DM views, the solo bot
agent, or a scripted test adapter). A DecisionResponse is the answer, expressed
in JSON-serializable form (indices into the request's option list, a number, or
text) so responses can be appended to the per-game decision log and replayed
after a bot restart.

This module must stay import-light: no discord, no views, no effect engine
(TYPE_CHECKING only), so headless tools can import it without a bot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Decision kinds. One per distinct prompt protocol in the game.
KIND_EFFECT_CARD_SELECT = 'effect_card_select'
KIND_EFFECT_NUMBER_SELECT = 'effect_number_select'
KIND_EFFECT_TEXT_INPUT = 'effect_text_input'
KIND_REDRAW = 'redraw'
KIND_CARD_SELECT = 'card_select'
KIND_TWO_STEP_CARD_SELECT = 'two_step_card_select'
KIND_TCG_SWITCH = 'tcg_switch'

# Response payload types.
PAYLOAD_INDICES = 'indices'
PAYLOAD_NUMBER = 'number'
PAYLOAD_TEXT = 'text'
PAYLOAD_CARD_KEYS = 'card_keys'
PAYLOAD_TIMEOUT = 'timeout'

# Request purposes: extra routing context for adapters that answer the same
# kind differently depending on where it came from (see BotAgentDecisionAdapter).
PURPOSE_INITIAL_BATTLE_CARD = 'initial_battle_card'
PURPOSE_SET_CARDS = 'set_cards'
PURPOSE_EFFECT_ORDER = 'effect_order'


@dataclass(frozen=True)
class DecisionOption:
    """One selectable option, identified by its index into the live list."""
    label: str
    description: str
    value_index: int


def build_card_options(cards: list[Any]) -> list[DecisionOption]:
    """Build serializable options for a list of Card or CardInstance objects."""
    options: list[DecisionOption] = []
    for value_index, card_or_instance in enumerate(cards):
        card = getattr(card_or_instance, 'card', card_or_instance)
        options.append(DecisionOption(
            label=f'{card.pack:02d}-{card.id:03d}',
            description=card.name,
            value_index=value_index,
        ))
    return options


@dataclass
class DecisionRequest:
    kind: str
    player_index: int
    prompt_text: str
    placeholder: str = ''
    options: list[DecisionOption] = field(default_factory=list)
    minimum_value: int = 0                     # number selection range
    maximum_value: int = 0
    label_prefix: Optional[str] = None         # number selection option labels
    modal_title: str = ''                      # text input modal
    button_label: str = ''
    input_label: Optional[str] = None
    input_placeholder: Optional[str] = None
    validator: Any = None                      # callable; never serialized
    minimum_selections: int = 1                # card selection counts
    maximum_selections: int = 1
    timeout_seconds: float = 300.0
    purpose: str = ''                          # adapter routing context
    opponent_name: str = 'opponent'            # used in waiting messages
    display_embed: Any = None                  # discord.Embed shown on reselect; never serialized
    # Assigned by the broker at request() entry, in deterministic code order.
    sequence_number: int = -1
    # The live CardInstance/Card list the options index into; never serialized.
    live_objects: Any = None


@dataclass
class DecisionResponse:
    sequence_number: int
    payload_type: str    # PAYLOAD_INDICES | PAYLOAD_NUMBER | PAYLOAD_TEXT | PAYLOAD_CARD_KEYS | PAYLOAD_TIMEOUT
    payload: Any


def request_fingerprint(request: DecisionRequest) -> dict[str, Any]:
    """
    The shape of a request that must match between the logged game and a
    replayed game for the log entry to be trusted. Deliberately coarse:
    it detects structural divergence (different prompt sequence after a code
    change) without being brittle about display text.
    """
    return {
        'kind': request.kind,
        'player_index': request.player_index,
        'option_count': len(request.options),
        'minimum_value': request.minimum_value,
        'maximum_value': request.maximum_value,
    }
