from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_056(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +50 if the opponent's Abyss has no cards."""
    log.debug('[%s] %s: entering effect_04_056', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if len(opponent.abyss) == 0:
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 50, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content="**Effect (04-056):** Opponent's Abyss is empty. Attack +50!")
        await engine._send_dm(opponent_index, content='**Effect (04-056):** Your Abyss is empty. Opponent gains Attack +50.')
    else:
        await engine._send_dm(player_index, content=f"**Effect (04-056):** Opponent has {len(opponent.abyss)} card(s) in Abyss. No effect.")
