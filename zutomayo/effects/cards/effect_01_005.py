from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_005(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Reverses the opponent's attack power day and night.
    *Effective after the opponent's enchantment is activated.
    We set the flag; it takes effect when attack power is calculated.
    """
    opponent_index = 1 - player_index
    engine.turn_state.day_night_reversed[opponent_index] = True
    log.debug('[%s] %s: set day_night_reversed for opponent %s to True', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index))
