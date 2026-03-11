from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import MIDNIGHT
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_036(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """The clock changes to midnight if there are two or more flame cards in the Abyss."""
    player = game_state.players[player_index]
    flame_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.FLAME)
    log.debug('[%s] %s: flame cards in abyss = %d (need >= 2)', card_instance.card.effect, engine.player_label(player_index), flame_count)
    if flame_count >= 2:
        old_chronos = game_state.chronos
        engine.set_chronos(game_state, MIDNIGHT)
        log.debug('[%s] %s: condition met, clock changed to midnight (from %d to %d)', card_instance.card.effect, engine.player_label(player_index), old_chronos, game_state.chronos)
    else:
        log.debug('[%s] %s: condition not met, clock unchanged', card_instance.card.effect, engine.player_label(player_index))
