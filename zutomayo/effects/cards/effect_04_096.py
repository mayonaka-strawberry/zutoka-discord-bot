from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_096(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """If the card in the Battle Zone is a (Neko Reset) character, reduce damage by 50 when you take damage."""
    log.debug('[%s] %s: entering effect_04_096', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if (player.battle_zone is not None
            and player.battle_zone.card.song == Song.NEKO_RESET):
        engine.turn_state.damage_reduction[player_index] += 50
        log.debug('[%s] %s: damage reduction +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 50, engine.turn_state.damage_reduction[player_index])
        await engine._send_dm(
            player_index,
            content='**Effect (04-096):** Battle Zone character is Neko Reset. Damage reduced by 50!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-096):** Opponent has Neko Reset in Battle Zone. Damage reduced by 50.',
        )
    else:
        await engine._send_dm(player_index, content='**Effect (04-096):** Battle Zone character is not Neko Reset. No effect.')
