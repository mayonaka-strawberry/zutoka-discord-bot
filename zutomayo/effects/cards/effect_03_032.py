from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_zone_attribute_count_at_least
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_03_032(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +80 if there are at least three cards of the wind attribute in the Abyss."""
    await add_attack_bonus_if_zone_attribute_count_at_least(
        engine, game_state, player_index, card_instance,
        zone_attribute_name='abyss', attribute=Attribute.WIND,
        minimum_count=3, bonus=80,
    )
