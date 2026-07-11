from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_zone_attribute_count_at_least
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_03_057(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +70 if there are 4 or more cards of the flame attribute in the Abyss."""
    await add_attack_bonus_if_zone_attribute_count_at_least(
        engine, game_state, player_index, card_instance,
        zone_attribute_name='abyss', attribute=Attribute.FLAME,
        minimum_count=4, bonus=70,
    )
