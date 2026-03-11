from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_030(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +50 if it's daytime."""
    if game_state.day_night == Chronos.DAY:
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: it is daytime, +50 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: it is not daytime, no bonus', card_instance.card.effect, engine.player_label(player_index))
