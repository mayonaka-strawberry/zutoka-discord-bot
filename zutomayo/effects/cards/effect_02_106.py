from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import NOON
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)



async def effect_02_106(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If the Abyss has two or more cards of the wind attribute, the clock changes to noon."""
    player = game_state.players[player_index]
    wind_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.WIND)
    log.debug('[%s] %s: wind cards in abyss = %d (need >= 2)', card_instance.card.effect, engine.player_label(player_index), wind_count)
    if wind_count >= 2:
        old_chronos = game_state.chronos
        engine.set_chronos(game_state, NOON)
        log.debug('[%s] %s: condition met, clock changed to noon (from %d to %d)', card_instance.card.effect, engine.player_label(player_index), old_chronos, game_state.chronos)
    else:
        log.debug('[%s] %s: condition not met, clock unchanged', card_instance.card.effect, engine.player_label(player_index))
