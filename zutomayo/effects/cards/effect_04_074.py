from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_074(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Opponent's Attack -30 if you swapped with a (SHADE) character this turn."""
    log.debug('[%s] %s: entering effect_04_074', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index

    if Song.SHADE in engine.turn_state.swapped_from_songs.get(player_index, set()):
        engine.turn_state.attack_bonus[opponent_index] -= 30
        log.debug('[%s] %s: opponent attack penalty -%d (opponent bonus now %d)', card_instance.card.effect, engine.player_label(player_index), 30, engine.turn_state.attack_bonus[opponent_index])
        await engine._send_dm(player_index, content="**Effect (04-074):** Swapped with a SHADE character this turn. Opponent's Attack -30!")
        await engine._send_dm(opponent_index, content='**Effect (04-074):** Opponent swapped with a SHADE character. Your Attack -30.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-074):** Did not swap with a SHADE character this turn. No effect.')
