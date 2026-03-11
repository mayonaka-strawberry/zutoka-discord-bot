from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_029(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +50 if it's daytime."""
    log.debug('[%s] %s: checking day/night (current=%s)', card_instance.card.effect, engine.player_label(player_index), game_state.day_night)
    if game_state.day_night == Chronos.DAY:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: it is daytime, attack bonus +50 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: not daytime, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
