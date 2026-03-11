from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_056(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +50 if there are 3 or fewer cards on the Power Charger."""
    player = game_state.players[player_index]
    pc_count = len(player.power_charger)
    log.debug('[%s] %s: power charger count=%d (need <= 3)', card_instance.card.effect, engine.player_label(player_index), pc_count)
    if pc_count <= 3:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: condition met, attack bonus +50 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: too many cards on power charger, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
