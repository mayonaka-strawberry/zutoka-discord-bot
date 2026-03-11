from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_085(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    At the end of the turn, if it is daytime, advance the clock by 2.

    If you take more than 30 damage in this turn, immediately place this card in the Abyss.

    This is a persistent area enchant effect. The actual clock advance and removal
    are handled in process_end_of_turn_effects().
    """
    log.debug('[%s] %s: passive effect, checked elsewhere', card_instance.card.effect, engine.player_label(player_index))
