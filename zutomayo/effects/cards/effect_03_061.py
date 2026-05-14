from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_061(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Set all cards' clocks to 1. At the end of the turn, if the opponent has
    an Area Enchantment card on the field, this card is placed on the Power
    Charger.

    This is a persistent area enchant effect.
    Clock override is checked in should_override_all_clocks() during advance_chronos.
    End-of-turn self-removal is handled in check_area_enchant_removal().
    """
    log.debug('[%s] %s: passive effect, checked elsewhere', card_instance.card.effect, engine.player_label(player_index))
