from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_102(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +40 if all the cards in the Power Charger are of the wind attribute."""
    player = game_state.players[player_index]
    log.debug('[%s] %s: checking power charger (count=%d)', card_instance.card.effect, engine.player_label(player_index), len(player.power_charger))
    if player.power_charger and all(card_instance.card.attribute == Attribute.WIND for card_instance in player.power_charger):
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: all power charger cards are wind, attack bonus +40 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: condition not met (empty or not all wind), no bonus applied', card_instance.card.effect, engine.player_label(player_index))
