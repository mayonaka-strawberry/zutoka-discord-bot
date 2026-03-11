from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_102(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if it's daytime."""
    if game_state.day_night == Chronos.DAY:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: it is DAY, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: it is not DAY (day_night=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), game_state.day_night)
