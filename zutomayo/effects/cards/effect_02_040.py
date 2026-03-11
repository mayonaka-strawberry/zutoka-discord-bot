from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_040(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +30 if there is a darkness card in the Power charger."""
    player = game_state.players[player_index]
    if any(card_instance.card.attribute == Attribute.DARKNESS for card_instance in player.power_charger):
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: darkness card in power charger, +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: no darkness card in power charger, no bonus', card_instance.card.effect, engine.player_label(player_index))
