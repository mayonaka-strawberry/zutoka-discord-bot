from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_058(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +20 if the opponent's character card has a power cost of 2 or more."""
    opponent = game_state.players[1 - player_index]
    if opponent.battle_zone is not None and opponent.battle_zone.card.power_cost >= 2:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: opponent power_cost=%d >= 2, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.card.power_cost, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: opponent power_cost < 2 or no battle_zone, no bonus', card_instance.card.effect, engine.player_label(player_index))
