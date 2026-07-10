from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_zone_cards_all_attribute
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_02_099(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +30 if there is only wind card in the Abyss."""
    await add_attack_bonus_if_zone_cards_all_attribute(
        engine, game_state, player_index, card_instance,
        zone_attribute_name='abyss', attribute=Attribute.WIND, bonus=30,
    )
