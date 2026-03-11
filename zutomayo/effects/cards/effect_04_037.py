from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_037(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if all cards in your Power Charger are darkness attribute."""
    log.debug('[%s] %s: entering effect_04_037', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    if player.power_charger and all(
        power_card.card.attribute == Attribute.DARKNESS for power_card in player.power_charger
    ):
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 20, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content='**Effect (04-037):** All Power Charger cards are darkness. Attack +20!')
        await engine._send_dm(1 - player_index, content='**Effect (04-037):** Opponent has all darkness Power Charger. Attack +20.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-037):** Power Charger is not all darkness. No effect.')
