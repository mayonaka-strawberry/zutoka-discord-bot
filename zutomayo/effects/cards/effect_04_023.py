from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_023(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +100 if you swapped with a (STUDY ME) character this turn."""
    log.debug('[%s] %s: entering effect_04_023', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index

    if Song.STUDY_ME in engine.turn_state.swapped_from_songs.get(player_index, set()):
        engine.turn_state.attack_bonus[player_index] += 100
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 100, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content='**Effect (04-023):** Swapped with a STUDY ME character this turn. Attack +100!')
        await engine._send_dm(opponent_index, content='**Effect (04-023):** Opponent swapped with a STUDY ME character. Attack +100.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-023):** Did not swap with a STUDY ME character this turn. No effect.')
