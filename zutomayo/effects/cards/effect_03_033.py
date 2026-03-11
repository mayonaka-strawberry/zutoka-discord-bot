from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import CHRONOS_SIZE
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_033(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If it's daytime, advance the clocks by two."""
    log.debug('[%s] %s: checking day/night (current=%s)', card_instance.card.effect, engine.player_label(player_index), game_state.day_night)
    if game_state.day_night == Chronos.DAY:
        old_chronos = game_state.chronos
        new_chronos = (game_state.chronos + 2) % CHRONOS_SIZE
        log.debug('[%s] %s: it is daytime, advancing clock by 2 (chronos %d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_chronos, new_chronos)
        engine.set_chronos(game_state, new_chronos)
    else:
        log.debug('[%s] %s: not daytime, no clock advance', card_instance.card.effect, engine.player_label(player_index))
