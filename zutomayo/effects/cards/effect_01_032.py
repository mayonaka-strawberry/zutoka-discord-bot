from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_attack_bonus_if_own_hp_at_most


if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_01_032(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +50 if your HP is at or below 30."""
    await add_attack_bonus_if_own_hp_at_most(
        engine, game_state, player_index, card_instance,
        hp_threshold=30, bonus=50,
    )
