from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_059(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """HP +50 if there are 3 or more attributes in the Power Charger."""
    log.debug('[%s] %s: entering effect_04_059', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    power_charger_attributes = {power_card.card.attribute for power_card in player.power_charger}

    if len(power_charger_attributes) >= 3:
        player.hp = min(player.hp + 50, 100)
        log.debug('[%s] %s: HP +50 (now %d)', card_instance.card.effect, engine.player_label(player_index), player.hp)
        attribute_names = ', '.join(attribute.value for attribute in power_charger_attributes)
        await engine._send_dm(player_index, content=f'**Effect (04-059):** Power Charger has {len(power_charger_attributes)} attributes ({attribute_names}). HP +50!')
        await engine._send_dm(opponent_index, content=f'**Effect (04-059):** Opponent has {len(power_charger_attributes)} attributes in Power Charger. HP +50.')
    else:
        await engine._send_dm(player_index, content=f'**Effect (04-059):** Power Charger has only {len(power_charger_attributes)} attribute(s). Need 3+. No effect.')
