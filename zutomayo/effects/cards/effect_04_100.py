from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_100(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    If the card in the Battle Zone is a (Neko Reset) character, deal damage to your opponent
    at the end of the turn equal to the amount reduced this turn.

    The actual damage reflection is handled in process_end_of_turn_effects().
    """
    log.debug('[%s] %s: entering effect_04_100', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if (player.battle_zone is not None
            and player.battle_zone.card.song == Song.NEKO_RESET):
        engine.turn_state.reflect_reduction[player_index] = True
        log.debug('[%s] %s: enabled damage reflection', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (04-100):** Battle Zone character is Neko Reset. Damage reduced this turn will be reflected to opponent at end of turn!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-100):** Opponent has Neko Reset in Battle Zone. Reduced damage will be reflected back to you.',
        )
    else:
        await engine._send_dm(player_index, content='**Effect (04-100):** Battle Zone character is not Neko Reset. No effect.')
