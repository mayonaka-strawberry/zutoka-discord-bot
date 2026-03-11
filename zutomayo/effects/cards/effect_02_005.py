from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState

log = logging.getLogger(__name__)


async def effect_02_005(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Disable the clock of your opponent's character card.

    This is a persistent area enchant effect. The actual clock disabling is checked
    in is_opponent_clock_disabled() during advance_chronos. This handler is a no-op
    since the effect is passive.
    Removal condition (day/night switch) is handled in check_area_enchant_removal().
    """
    log.debug('[%s] %s: passive effect, checked elsewhere', card_instance.card.effect, engine.player_label(player_index))
