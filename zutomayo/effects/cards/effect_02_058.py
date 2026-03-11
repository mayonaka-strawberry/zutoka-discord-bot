from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_058(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """+1 power star for each darkness card in the Abyss."""
    player = game_state.players[player_index]
    darkness_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.DARKNESS)
    engine.turn_state.power_bonus[player_index] += darkness_count
    log.debug('[%s] %s: %d darkness cards in abyss, +%d power bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), darkness_count, darkness_count, engine.turn_state.power_bonus[player_index])
