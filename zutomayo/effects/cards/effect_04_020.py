from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_020(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 for each electric card in the Abyss."""
    log.debug('[%s] %s: entering effect_04_020', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    electricity_count = sum(1 for abyss_card in player.abyss if abyss_card.card.attribute == Attribute.ELECTRICITY)

    if electricity_count > 0:
        attack_bonus = electricity_count * 20
        engine.turn_state.attack_bonus[player_index] += attack_bonus
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), attack_bonus, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content=f'**Effect (04-020):** {electricity_count} electric card(s) in Abyss. Attack +{attack_bonus}!')
        await engine._send_dm(1 - player_index, content=f'**Effect (04-020):** Opponent has {electricity_count} electric card(s) in Abyss. Attack +{attack_bonus}.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-020):** No electric cards in Abyss. No effect.')
