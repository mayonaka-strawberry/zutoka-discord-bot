from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_066(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +30 if the opponent's Character's SEND TO POWER is ★★."""
    log.debug('[%s] %s: entering effect_04_066', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if opponent.battle_zone is not None and opponent.battle_zone.card.send_to_power == 2:
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 30, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content="**Effect (04-066):** Opponent's character has SEND TO POWER ★★. Attack +30!")
        await engine._send_dm(opponent_index, content='**Effect (04-066):** Your character has SEND TO POWER ★★. Opponent gains Attack +30.')
    else:
        send_to_power = opponent.battle_zone.card.send_to_power if opponent.battle_zone is not None else 0
        await engine._send_dm(player_index, content=f"**Effect (04-066):** Opponent's character SEND TO POWER is {send_to_power}. No effect.")
