from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_014(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If the Abyss has two or more cards of the darkness attribute, recover 20 HP."""
    player = game_state.players[player_index]
    darkness_count = sum(1 for card_instance in player.abyss if card_instance.card.attribute == Attribute.DARKNESS)
    log.debug('[%s] %s: darkness cards in abyss = %d (need >= 2)', card_instance.card.effect, engine.player_label(player_index), darkness_count)
    if darkness_count >= 2:
        old_hp = player.hp
        player.hp = min(player.hp + 20, 100)
        log.debug('[%s] %s: condition met, +20 HP (from %d to %d)', card_instance.card.effect, engine.player_label(player_index), old_hp, player.hp)
    else:
        log.debug('[%s] %s: condition not met, no HP recovery', card_instance.card.effect, engine.player_label(player_index))
