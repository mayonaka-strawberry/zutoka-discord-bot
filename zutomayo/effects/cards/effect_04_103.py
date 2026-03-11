from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_103(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +40 if all cards in your Abyss are wind attribute."""
    log.debug('[%s] %s: entering effect_04_103', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if player.abyss and all(abyss_card.card.attribute == Attribute.WIND for abyss_card in player.abyss):
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 40, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(
            player_index,
            content='**Effect (04-103):** All cards in Abyss are wind attribute. Attack +40!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-103):** All cards in opponent\'s Abyss are wind attribute. Attack +40.',
        )
    else:
        if not player.abyss:
            await engine._send_dm(player_index, content='**Effect (04-103):** Abyss is empty. No effect.')
        else:
            await engine._send_dm(player_index, content='**Effect (04-103):** Not all cards in Abyss are wind attribute. No effect.')
