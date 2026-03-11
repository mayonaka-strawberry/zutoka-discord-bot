from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_083(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +60 if there are 4 or more cards of the darkness attribute in the Abyss."""
    player = game_state.players[player_index]
    darkness_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.DARKNESS)
    log.debug('[%s] %s: darkness cards in abyss=%d (need >= 4)', card_instance.card.effect, engine.player_label(player_index), darkness_count)
    if darkness_count >= 4:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 60
        log.debug('[%s] %s: condition met, attack bonus +60 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: not enough darkness cards, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
