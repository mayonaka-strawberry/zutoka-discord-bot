from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_008(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Clock disabled. Return to the time at the beginning of this turn."""
    # Revert to turn start time — this does NOT track as a new transition,
    # because Q&A says the original transitions that occurred are preserved.
    log.debug('[%s] %s: reverting chronos from %d to turn-start value %d', card_instance.card.effect, engine.player_label(player_index), game_state.chronos, game_state.chronos_at_turn_start)
    game_state.chronos = game_state.chronos_at_turn_start
