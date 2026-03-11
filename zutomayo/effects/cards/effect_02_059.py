from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_059(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If it is daytime, +30 attack power of your character cards played at the same time."""
    player = game_state.players[player_index]
    if (game_state.day_night == Chronos.DAY
            and player.battle_zone is not None
            and player.battle_zone.played_this_turn):
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: daytime + character played this turn, +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: condition not met (day_night=%s, played_this_turn=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), game_state.day_night, player.battle_zone.played_this_turn if player.battle_zone else None)
