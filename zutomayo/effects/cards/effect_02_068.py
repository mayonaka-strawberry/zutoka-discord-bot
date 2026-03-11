from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_068(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +30 if the attribute of the character card used in the previous turn is flame and it's night."""
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is not None and prev_char.attribute == Attribute.FLAME and game_state.day_night == Chronos.NIGHT:
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: prev Flame + night, +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: condition not met (prev_char=%s, day_night=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), prev_char.attribute if prev_char else None, game_state.day_night)
