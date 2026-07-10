from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_per_matching_card_in_zone
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_02_064(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 for each electricity card in the Power charger."""
    await add_attack_bonus_per_matching_card_in_zone(
        engine, game_state, player_index, card_instance,
        card_matcher=lambda zone_card: zone_card.card.attribute == Attribute.ELECTRICITY,
        matched_description='ELECTRICITY',
        zone_attribute_name='power_charger', bonus_per_card=20,
    )
