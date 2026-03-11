from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_099(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +30 if there is only wind card in the Abyss."""
    player = game_state.players[player_index]
    if player.abyss and all(card_instance.card.attribute == Attribute.WIND for card_instance in player.abyss):
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: all abyss cards are wind (%d cards), +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), len(player.abyss), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: abyss not all wind (count=%d), no bonus', card_instance.card.effect, engine.player_label(player_index), len(player.abyss))
