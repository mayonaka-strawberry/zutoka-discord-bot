from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_095(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if the attribute of the opponent's character card is darkness or electric."""
    opponent = game_state.players[1 - player_index]
    if opponent.battle_zone is not None and opponent.battle_zone.effective_attribute in (
        Attribute.DARKNESS, Attribute.ELECTRICITY,
    ):
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: opponent attribute is %s (DARKNESS or ELECTRICITY), +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.effective_attribute, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: opponent attribute is not DARKNESS or ELECTRICITY (battle_zone=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.effective_attribute if opponent.battle_zone else None)
