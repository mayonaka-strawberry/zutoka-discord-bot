from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_030(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Attack +40 if there are no cards in your opponent's Abyss.

    When a card is placed in the opponent's Abyss, immediately place this card
    in the Abyss (handled by check_area_enchant_removal).
    """
    log.debug('[%s] %s: entering effect_04_030', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if len(opponent.abyss) == 0:
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 40, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content="**Effect (04-030):** Opponent's Abyss is empty. Attack +40!")
        await engine._send_dm(opponent_index, content='**Effect (04-030):** Your Abyss is empty. Opponent gains Attack +40.')
    else:
        await engine._send_dm(player_index, content=f"**Effect (04-030):** Opponent has {len(opponent.abyss)} card(s) in Abyss. No bonus.")
