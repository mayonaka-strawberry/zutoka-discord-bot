from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_084(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """+10 to your attack power and the opponent's character attribute changes to electric while on the battlefield."""
    engine.turn_state.attack_bonus[player_index] += 10
    log.debug('[%s] %s: +10 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), engine.turn_state.attack_bonus[player_index])

    opponent = game_state.players[1 - player_index]
    if opponent.battle_zone is not None:
        old_attr = opponent.battle_zone.effective_attribute
        opponent.battle_zone.attribute_override = Attribute.ELECTRICITY
        log.debug('[%s] %s: opponent character attribute changed from %s to Electricity', card_instance.card.effect, engine.player_label(player_index), old_attr)
    else:
        log.debug('[%s] %s: opponent has no character in battle zone, no attribute change', card_instance.card.effect, engine.player_label(player_index))
