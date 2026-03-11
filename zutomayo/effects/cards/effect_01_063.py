from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_063(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Reversing one's basic attack power day and night."""
    engine.turn_state.day_night_reversed[player_index] = True
    log.debug('[%s] %s: set own day_night_reversed to True', card_instance.card.effect, engine.player_label(player_index))
