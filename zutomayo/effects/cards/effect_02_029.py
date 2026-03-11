from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_029(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If the opponent's character card has a Power cost of 6 or more, attack power +50."""
    opponent = game_state.players[1 - player_index]
    if opponent.battle_zone is not None and opponent.battle_zone.card.power_cost >= 6:
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: opponent power cost %d >= 6, +50 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.card.power_cost, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: opponent power cost %s < 6 or no character, no bonus', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.card.power_cost if opponent.battle_zone else None)
