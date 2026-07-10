from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_any_attribute_card_in_zone
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_02_035(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if there is a flame card in the Power charger."""
    await add_attack_bonus_if_any_attribute_card_in_zone(
        engine, game_state, player_index, card_instance,
        zone_attribute_name='power_charger', attribute=Attribute.FLAME, bonus=20,
    )
