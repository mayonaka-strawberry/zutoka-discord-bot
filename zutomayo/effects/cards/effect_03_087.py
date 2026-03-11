from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_087(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +40 if the opponent's HP is 100."""
    opponent = game_state.players[1 - player_index]
    log.debug('[%s] %s: checking opponent HP (opponent HP=%d)', card_instance.card.effect, engine.player_label(player_index), opponent.hp)
    if opponent.hp == 100:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: opponent HP is 100, attack bonus +40 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: opponent HP is not 100, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
