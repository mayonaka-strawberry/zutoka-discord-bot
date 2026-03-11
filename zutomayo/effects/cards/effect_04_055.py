from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.card_type import CardType
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_055(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """HP +20 if the card in the Battle Zone is a (TAIDADA) character."""
    log.debug('[%s] %s: entering effect_04_055', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if (player.battle_zone is not None
            and player.battle_zone.card.card_type == CardType.CHARACTER
            and player.battle_zone.card.song == Song.TAIDADA):
        player.hp = min(player.hp + 20, 100)
        log.debug('[%s] %s: HP +20 (now %d)', card_instance.card.effect, engine.player_label(player_index), player.hp)
        await engine._send_dm(player_index, content='**Effect (04-055):** Battle Zone character is TAIDADA. HP +20!')
        await engine._send_dm(opponent_index, content='**Effect (04-055):** Opponent has TAIDADA character in Battle Zone. HP +20.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-055):** Battle Zone character is not TAIDADA. No effect.')
