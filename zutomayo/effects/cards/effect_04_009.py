from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_009(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """If there are 3 or more cards in your Abyss, opponent's Character gains -30 Attack."""
    log.debug('[%s] %s: entering effect_04_009', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if len(player.abyss) >= 3:
        engine.turn_state.attack_bonus[opponent_index] -= 30
        log.debug('[%s] %s: opponent attack penalty -%d (opponent bonus now %d)', card_instance.card.effect, engine.player_label(player_index), 30, engine.turn_state.attack_bonus[opponent_index])
        await engine._send_dm(player_index, content=f'**Effect (04-009):** {len(player.abyss)} cards in Abyss. Opponent\'s Attack -30!')
        await engine._send_dm(opponent_index, content=f'**Effect (04-009):** Opponent has {len(player.abyss)} cards in Abyss. Your Attack -30.')
    else:
        await engine._send_dm(player_index, content=f'**Effect (04-009):** Only {len(player.abyss)} card(s) in Abyss. Need 3+. No effect.')
