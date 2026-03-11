from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_075(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Opponent's HP -20 if you swapped with a (SHADE) character this turn."""
    log.debug('[%s] %s: entering effect_04_075', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if Song.SHADE in engine.turn_state.swapped_from_songs.get(player_index, set()):
        opponent.hp = max(0, opponent.hp - 20)
        log.debug('[%s] %s: opponent HP -20 (now %d)', card_instance.card.effect, engine.player_label(player_index), opponent.hp)
        await engine._send_dm(player_index, content="**Effect (04-075):** Swapped with a SHADE character this turn. Opponent's HP -20!")
        await engine._send_dm(opponent_index, content='**Effect (04-075):** Opponent swapped with a SHADE character. Your HP -20.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-075):** Did not swap with a SHADE character this turn. No effect.')
