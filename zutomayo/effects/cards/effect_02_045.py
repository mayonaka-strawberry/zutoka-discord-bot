from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_any_attribute_card_in_zone
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_02_045(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if the Power charger has a wind card."""
    await add_attack_bonus_if_any_attribute_card_in_zone(
        engine, game_state, player_index, card_instance,
        zone_attribute_name='power_charger', attribute=Attribute.WIND, bonus=20,
    )
