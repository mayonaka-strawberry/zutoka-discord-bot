from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import PlacementDestination, place_hand_cards_then_draw_same_count
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_04_062(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Place any number of darkness cards from your hand into the Abyss. If you do, draw the same number of cards."""
    await place_hand_cards_then_draw_same_count(
        engine, game_state, player_index, card_instance,
        candidate_matcher=lambda hand_card: hand_card.card.attribute == Attribute.DARKNESS,
        candidate_label='darkness card',
        no_candidates_message='No darkness cards in hand. No effect.',
        success_owner_template='**Effect (04-062):** Placed {count} card(s) into the Abyss: {names}.',
        success_opponent_template='**Effect (04-062):** Opponent placed {count} darkness card(s) into their Abyss.',
        destination=PlacementDestination.ABYSS,
    )
