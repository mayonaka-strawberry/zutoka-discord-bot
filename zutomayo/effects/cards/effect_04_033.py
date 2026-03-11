from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_033(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Attack +20 if all cards in your Abyss are wind attribute.

    When a card is placed into your Power Charger, immediately place this card
    into your Abyss (handled by check_area_enchant_removal).
    """
    log.debug('[%s] %s: entering effect_04_033', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if player.abyss and all(abyss_card.card.attribute == Attribute.WIND for abyss_card in player.abyss):
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 20, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content='**Effect (04-033):** All Abyss cards are wind attribute. Attack +20!')
        await engine._send_dm(opponent_index, content='**Effect (04-033):** Opponent has all wind Abyss. Attack +20.')
    elif not player.abyss:
        # Empty abyss — vacuously "all wind" is debatable; treating empty as no bonus
        await engine._send_dm(player_index, content='**Effect (04-033):** Abyss is empty. No effect.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-033):** Abyss contains non-wind cards. No effect.')
