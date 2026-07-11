from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_day_night
from zutomayo.enums.chronos import Chronos


if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_02_025(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +50 if it's night."""
    await add_attack_bonus_if_day_night(
        engine, game_state, player_index, card_instance,
        required_day_night=Chronos.NIGHT, bonus=50,
    )
