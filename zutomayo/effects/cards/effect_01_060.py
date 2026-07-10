from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_opponent_character_attribute
from zutomayo.enums.attribute import Attribute


if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_01_060(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +30 if the attribute of the opponent's character card is darkness."""
    await add_attack_bonus_if_opponent_character_attribute(
        engine, game_state, player_index, card_instance,
        attributes=(Attribute.DARKNESS,), bonus=30,
    )
