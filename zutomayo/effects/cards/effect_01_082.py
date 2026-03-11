from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_082(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if your character card's attribute is darkness."""
    player = game_state.players[player_index]
    if player.battle_zone is not None and player.battle_zone.effective_attribute == Attribute.DARKNESS:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: own attribute is DARKNESS, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: own attribute is not DARKNESS (battle_zone=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), player.battle_zone.effective_attribute if player.battle_zone else None)
