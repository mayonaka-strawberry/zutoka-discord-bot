from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_032(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +80 if there are at least three cards of the wind attribute in the Abyss."""
    player = game_state.players[player_index]
    wind_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.WIND)
    log.debug('[%s] %s: wind cards in abyss=%d (need >= 3)', card_instance.card.effect, engine.player_label(player_index), wind_count)
    if wind_count >= 3:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 80
        log.debug('[%s] %s: condition met, attack bonus +80 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: not enough wind cards, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
