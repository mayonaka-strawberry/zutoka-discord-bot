from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_026(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If it is night, +30 attack power of your character cards played at the same time."""
    if game_state.day_night != Chronos.NIGHT:
        log.debug('[%s] %s: it is not night, skipping', card_instance.card.effect, engine.player_label(player_index))
        return

    player = game_state.players[player_index]
    battle_char = player.battle_zone
    if battle_char is not None and battle_char.played_this_turn:
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: night + character played this turn, +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: night but no character played this turn, no bonus', card_instance.card.effect, engine.player_label(player_index))
