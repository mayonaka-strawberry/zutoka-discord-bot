from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import place_one_hand_card_then_draw_one
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_04_058(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Place a wind card from your hand into the Abyss. If you do, draw 1 card."""
    await place_one_hand_card_then_draw_one(
        engine, game_state, player_index, card_instance,
        candidate_matcher=lambda hand_card: hand_card.card.attribute == Attribute.WIND,
        candidate_label='wind', candidate_description='a wind card',
    )
