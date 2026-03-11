from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_030(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +70 if there are at least three cards of the electric attribute in the Abyss."""
    player = game_state.players[player_index]
    electric_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.ELECTRICITY)
    log.debug('[%s] %s: electric cards in abyss=%d (need >= 3)', card_instance.card.effect, engine.player_label(player_index), electric_count)
    if electric_count >= 3:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 70
        log.debug('[%s] %s: condition met, attack bonus +70 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: not enough electric cards, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
