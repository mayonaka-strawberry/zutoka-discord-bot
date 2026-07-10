from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_abyss_has_all_attributes
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_03_022(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +40 if there are cards of all 4 attributes (Darkness, Flame, Electricity, Wind) in the Abyss."""
    await add_attack_bonus_if_abyss_has_all_attributes(
        engine, game_state, player_index, card_instance,
        required_attributes=frozenset({Attribute.DARKNESS, Attribute.FLAME, Attribute.ELECTRICITY, Attribute.WIND}),
        bonus=40,
    )
