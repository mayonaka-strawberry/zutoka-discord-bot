from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_010(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +20 if the attribute of YOUR OWN character card used in the previous turn is flame."""
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is not None and prev_char.attribute == Attribute.FLAME:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: previous character was Flame, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: previous character was not Flame (prev_char=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), prev_char.attribute if prev_char else None)
