from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_by_opponent_character_power_cost


if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_01_101(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if the opponent's character card has a power cost of 0 or 1."""
    await add_attack_bonus_by_opponent_character_power_cost(
        engine, game_state, player_index, card_instance,
        maximum_power_cost=1, bonus=20,
    )
