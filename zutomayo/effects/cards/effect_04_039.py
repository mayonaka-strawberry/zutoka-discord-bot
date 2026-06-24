from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_039(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +40 if the opponent's character's attack is 0."""
    log.debug('[%s] %s: entering effect_04_039', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Use the engine's single source of truth so 04-099's attack_override and
    # the power-cost gate are honored (not a hand-rolled calculation).
    opponent_attack = engine.get_effective_attack(game_state, opponent)

    if opponent_attack == 0:
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 40, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content="**Effect (04-039):** Opponent's character attack is 0. Attack +40!")
        await engine._send_dm(opponent_index, content='**Effect (04-039):** Your character attack is 0. Opponent gains Attack +40.')
    else:
        await engine._send_dm(player_index, content=f"**Effect (04-039):** Opponent's character attack is {opponent_attack}. No effect.")
