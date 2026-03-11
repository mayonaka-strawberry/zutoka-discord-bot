from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_104(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Attack +10 for each wind card in the Abyss.

    If there are 4 or more cards in the Abyss, place this card in the Abyss
    at the end of the turn (handled by check_area_enchant_removal).
    """
    player = game_state.players[player_index]
    wind_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.WIND)
    bonus = 10 * wind_count
    old_bonus = engine.turn_state.attack_bonus[player_index]
    engine.turn_state.attack_bonus[player_index] += bonus
    log.debug('[%s] %s: %d wind cards in abyss, attack bonus +%d (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), wind_count, bonus, old_bonus, engine.turn_state.attack_bonus[player_index])
