from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_064(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +20 for each electricity card in the Power charger."""
    player = game_state.players[player_index]
    electric_count = sum(1 for card_instance in player.power_charger if card_instance.card.attribute == Attribute.ELECTRICITY)
    engine.turn_state.attack_bonus[player_index] += 20 * electric_count
    log.debug('[%s] %s: %d electricity cards in power charger, +%d attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), electric_count, 20 * electric_count, engine.turn_state.attack_bonus[player_index])
