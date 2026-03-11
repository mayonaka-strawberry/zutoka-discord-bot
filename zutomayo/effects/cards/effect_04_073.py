from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_073(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """HP +20 if you swapped with a (SHADE) character this turn."""
    log.debug('[%s] %s: entering effect_04_073', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    player = game_state.players[player_index]

    if Song.SHADE in engine.turn_state.swapped_from_songs.get(player_index, set()):
        player.hp = min(100, player.hp + 20)
        log.debug('[%s] %s: HP +20 (now %d)', card_instance.card.effect, engine.player_label(player_index), player.hp)
        await engine._send_dm(player_index, content='**Effect (04-073):** Swapped with a SHADE character this turn. HP +20!')
        await engine._send_dm(opponent_index, content='**Effect (04-073):** Opponent swapped with a SHADE character. HP +20.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-073):** Did not swap with a SHADE character this turn. No effect.')
