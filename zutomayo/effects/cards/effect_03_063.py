from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_063(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +60 if all the cards in the Abyss are of the electric attribute."""
    player = game_state.players[player_index]
    log.debug('[%s] %s: checking abyss (count=%d)', card_instance.card.effect, engine.player_label(player_index), len(player.abyss))
    if player.abyss and all(card_instance.card.attribute == Attribute.ELECTRICITY for card_instance in player.abyss):
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 60
        log.debug('[%s] %s: all abyss cards are electric, attack bonus +60 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: condition not met (empty or not all electric), no bonus applied', card_instance.card.effect, engine.player_label(player_index))
