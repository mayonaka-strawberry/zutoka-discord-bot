from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_062(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Character cards played at the same time do not have to be changed.

    Actual logic is handled in TurnManager.do_character_swap() before effects are processed.
    """
    log.debug('[%s] %s: passive effect, checked elsewhere', card_instance.card.effect, engine.player_label(player_index))
