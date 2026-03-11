from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_086(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +20 if it's night. If it's not night, the area enchant is removed (handled by check_area_enchant_removal)."""
    if game_state.day_night == Chronos.NIGHT:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: it is night, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: it is not night, no bonus (removal handled elsewhere)', card_instance.card.effect, engine.player_label(player_index))
