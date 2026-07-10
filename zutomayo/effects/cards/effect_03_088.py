from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_zone_cards_all_attribute
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_03_088(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +30 if all the cards in the Abyss are of the darkness attribute."""
    await add_attack_bonus_if_zone_cards_all_attribute(
        engine, game_state, player_index, card_instance,
        zone_attribute_name='abyss', attribute=Attribute.DARKNESS, bonus=30,
    )
