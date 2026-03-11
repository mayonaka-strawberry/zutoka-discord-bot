from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_089(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """20 damage reduction when you take damage."""
    engine.turn_state.damage_reduction[player_index] += 20
    log.debug('[%s] %s: +20 damage reduction (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.damage_reduction[player_index])
