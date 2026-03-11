from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_062(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Attack +30 if the attribute of the opponent's character card is flame."""
    opponent = game_state.players[1 - player_index]
    if opponent.battle_zone is not None and opponent.battle_zone.effective_attribute == Attribute.FLAME:
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: opponent attribute is FLAME, +30 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: opponent attribute is not FLAME (battle_zone=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.effective_attribute if opponent.battle_zone else None)
