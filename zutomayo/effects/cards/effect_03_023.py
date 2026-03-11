from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_023(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +40 if all the cards in the opponent's Power Charger are of the flame attribute."""
    opponent = game_state.players[1 - player_index]
    log.debug('[%s] %s: checking opponent power charger (count=%d)', card_instance.card.effect, engine.player_label(player_index), len(opponent.power_charger))
    if opponent.power_charger and all(card_instance.card.attribute == Attribute.FLAME for card_instance in opponent.power_charger):
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: all opponent power charger cards are flame, attack bonus +40 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: condition not met (empty or not all flame), no bonus applied', card_instance.card.effect, engine.player_label(player_index))
