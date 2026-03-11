from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_051(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +40 if there are cards of 3 different attributes in the Abyss."""
    player = game_state.players[player_index]
    abyss_attributes = {card_instance.card.attribute for card_instance in player.abyss}
    log.debug('[%s] %s: distinct abyss attributes=%d (need >= 3), found: %s', card_instance.card.effect, engine.player_label(player_index), len(abyss_attributes), abyss_attributes)
    if len(abyss_attributes) >= 3:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: 3+ attributes present, attack bonus +40 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: fewer than 3 attributes, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
