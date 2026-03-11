from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_026(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Two squares before and after midnight are also treated as midnight."""
    log.debug('[%s] %s: setting midnight_extended=True', card_instance.card.effect, engine.player_label(player_index))
    engine.turn_state.midnight_extended = True
    log.debug('[%s] %s: midnight_extended is now True', card_instance.card.effect, engine.player_label(player_index))
