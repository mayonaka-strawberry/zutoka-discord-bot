from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_009(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +50 if it's midnight."""
    is_midnight = engine.is_effectively_midnight(game_state)
    log.debug('[%s] %s: is_effectively_midnight=%s', card_instance.card.effect, engine.player_label(player_index), is_midnight)
    if is_midnight:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: attack bonus +50 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: not midnight, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
