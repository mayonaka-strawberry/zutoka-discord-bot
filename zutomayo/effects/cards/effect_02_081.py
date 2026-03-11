from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_081(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +20 if all cards in the Abyss are darkness (and abyss is non-empty)."""
    player = game_state.players[player_index]
    if player.abyss and all(card_instance.card.attribute == Attribute.DARKNESS for card_instance in player.abyss):
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: all abyss cards are darkness (%d cards), +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), len(player.abyss), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: abyss not all darkness (count=%d), no bonus', card_instance.card.effect, engine.player_label(player_index), len(player.abyss))
