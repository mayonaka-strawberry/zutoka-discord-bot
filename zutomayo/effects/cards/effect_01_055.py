from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_055(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +20 if your HP is at or below 50."""
    player = game_state.players[player_index]
    log.debug('[%s] %s: checking HP=%d <= 50', card_instance.card.effect, engine.player_label(player_index), player.hp)
    if player.hp <= 50:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: HP <= 50, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: HP > 50, no bonus', card_instance.card.effect, engine.player_label(player_index))
