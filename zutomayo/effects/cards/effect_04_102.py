from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_102(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +10 for each (STUDY ME) card in your Power Charger."""
    log.debug('[%s] %s: entering effect_04_102', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    study_me_count = sum(1 for power_card in player.power_charger if power_card.card.song == Song.STUDY_ME)

    if study_me_count > 0:
        attack_bonus = 10 * study_me_count
        engine.turn_state.attack_bonus[player_index] += attack_bonus
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), attack_bonus, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-102):** {study_me_count} STUDY ME card(s) in Power Charger. Attack +{attack_bonus}!',
        )
        await engine._send_dm(
            opponent_index,
            content=f'**Effect (04-102):** Opponent has {study_me_count} STUDY ME card(s) in Power Charger. Attack +{attack_bonus}.',
        )
    else:
        await engine._send_dm(player_index, content='**Effect (04-102):** No STUDY ME cards in Power Charger. No effect.')
