from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_065(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    If the card in the Battle Zone is a (STUDY ME), reduce that character's Power Cost by 2.

    When the character in the Battle Zone is swapped for a Character other than (STUDY ME),
    immediately place this card into the Abyss (handled by check_area_enchant_removal).
    """
    log.debug('[%s] %s: entering effect_04_065', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if (player.battle_zone is not None
            and player.battle_zone.card.song == Song.STUDY_ME):
        player.battle_zone.power_cost_reduction = 2
        log.debug('[%s] %s: power cost reduction set to %d', card_instance.card.effect, engine.player_label(player_index), 2)
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-065):** Battle Zone character is STUDY ME. Power Cost reduced by 2!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-065):** Opponent has STUDY ME in Battle Zone. Power Cost reduced by 2.',
        )
    else:
        await engine._send_dm(player_index, content='**Effect (04-065):** Battle Zone character is not STUDY ME. No Power Cost reduction.')
