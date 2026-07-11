from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_on_day_night_transition
from zutomayo.enums.chronos import Chronos


if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_01_090(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 when day changes to night."""
    await add_attack_bonus_on_day_night_transition(
        engine, game_state, player_index, card_instance,
        from_day_night=Chronos.DAY, to_day_night=Chronos.NIGHT, bonus=20,
    )
