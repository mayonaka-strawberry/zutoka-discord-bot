from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState
    

log = logging.getLogger(__name__)


async def effect_04_092(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """If the character in the Battle Zone is (SHADE), the opponent's Attack is reduced by 40."""
    log.debug('[%s] %s: entering effect_04_092', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if (player.battle_zone is not None
            and player.battle_zone.card.song == Song.SHADE):
        engine.turn_state.attack_bonus[opponent_index] -= 40
        log.debug('[%s] %s: opponent attack penalty -%d (opponent bonus now %d)', card_instance.card.effect, engine.player_label(player_index), 40, engine.turn_state.attack_bonus[opponent_index])
        await engine._send_dm(
            player_index,
            content="**Effect (04-092):** Battle Zone character is SHADE. Opponent's Attack reduced by 40!",
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-092):** Opponent has SHADE in Battle Zone. Your Attack is reduced by 40.',
        )
    else:
        await engine._send_dm(player_index, content='**Effect (04-092):** Battle Zone character is not SHADE. No effect.')
