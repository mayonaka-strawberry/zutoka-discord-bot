from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_031(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +100 if there are 4 or more attributes in the Power Charger."""
    log.debug('[%s] %s: entering effect_04_031', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if not player.power_charger:
        await engine._send_dm(player_index, content='**Effect (04-031):** Power Charger is empty. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    power_charger_attributes = {power_card.card.attribute for power_card in player.power_charger}

    if len(power_charger_attributes) >= 4:
        engine.turn_state.attack_bonus[player_index] += 100
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 100, engine.turn_state.attack_bonus[player_index])
        attribute_names = ', '.join(attribute.value for attribute in power_charger_attributes)
        await engine._send_dm(player_index, content=f'**Effect (04-031):** {len(power_charger_attributes)} attributes in Power Charger ({attribute_names}). Attack +100!')
        await engine._send_dm(opponent_index, content=f'**Effect (04-031):** Opponent has {len(power_charger_attributes)} attributes in Power Charger. Attack +100.')
    else:
        attribute_names = ', '.join(attribute.value for attribute in power_charger_attributes)
        await engine._send_dm(player_index, content=f'**Effect (04-031):** Only {len(power_charger_attributes)} attribute(s) in Power Charger ({attribute_names}). Need 4+. No effect.')
