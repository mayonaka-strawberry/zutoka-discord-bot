from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_007(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +50 if the Abyss's cards have four different attributes."""
    player = game_state.players[player_index]

    abyss_attributes = set()
    for ci in player.abyss:
        abyss_attributes.add(ci.card.attribute)

    log.debug('[%s] %s: abyss has %d distinct attributes: %s', card_instance.card.effect, engine.player_label(player_index), len(abyss_attributes), abyss_attributes)
    if len(abyss_attributes) >= 4:
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: 4+ attributes found, +50 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: fewer than 4 attributes, no bonus', card_instance.card.effect, engine.player_label(player_index))
