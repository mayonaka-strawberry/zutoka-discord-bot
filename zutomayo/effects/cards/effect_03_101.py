from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_101(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +40 if there are 2 or more cards in the Abyss."""
    player = game_state.players[player_index]
    abyss_count = len(player.abyss)
    log.debug('[%s] %s: abyss count=%d (need >= 2)', card_instance.card.effect, engine.player_label(player_index), abyss_count)
    if abyss_count >= 2:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: condition met, attack bonus +40 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: not enough cards in abyss, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
