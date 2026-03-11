from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import NIGHT_END
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_061(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +30 when day changes to night."""
    dn_at_start = Chronos.NIGHT if 0 <= game_state.chronos_at_turn_start <= NIGHT_END else Chronos.DAY
    log.debug('[%s] %s: turn started at %s, currently %s', card_instance.card.effect, engine.player_label(player_index), dn_at_start, game_state.day_night)
    if dn_at_start == Chronos.DAY and game_state.day_night == Chronos.NIGHT:
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: day->night transition detected, +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: no day->night transition, no bonus', card_instance.card.effect, engine.player_label(player_index))
