from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_024(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +110 if you swapped with a (STUDY ME) character this turn.
    Damage dealt by this character cannot be reduced.
    """
    log.debug('[%s] %s: entering effect_04_024', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index

    # "Damage dealt by this character cannot be reduced" is a separate clause
    # that applies unconditionally (both EN and JP text treat it as a standalone sentence).
    engine.turn_state.damage_not_reducible[player_index] = True
    log.debug('[%s] %s: damage cannot be reduced flag set', card_instance.card.effect, engine.player_label(player_index))

    if Song.STUDY_ME in engine.turn_state.swapped_from_songs.get(player_index, set()):
        engine.turn_state.attack_bonus[player_index] += 110
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 110, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content='**Effect (04-024):** Swapped with a STUDY ME character this turn. Attack +110! Damage cannot be reduced.')
        await engine._send_dm(opponent_index, content='**Effect (04-024):** Opponent swapped with a STUDY ME character. Attack +110. Damage cannot be reduced.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-024):** Did not swap with a STUDY ME character this turn. No attack bonus, but damage still cannot be reduced.')
        await engine._send_dm(opponent_index, content='**Effect (04-024):** Opponent did not swap with a STUDY ME character. No attack bonus, but damage still cannot be reduced.')
