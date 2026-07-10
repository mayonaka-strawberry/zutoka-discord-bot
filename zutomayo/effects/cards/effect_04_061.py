from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import PlacementDestination, place_hand_cards_then_draw_same_count

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_04_061(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Place any number of cards from your hand at the bottom of the deck. If you do, draw the same number of cards."""
    await place_hand_cards_then_draw_same_count(
        engine, game_state, player_index, card_instance,
        candidate_matcher=None, candidate_label='card',
        destination=PlacementDestination.DECK_BOTTOM,
    )
