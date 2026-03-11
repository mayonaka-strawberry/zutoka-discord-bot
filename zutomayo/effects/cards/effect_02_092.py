from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_092(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Attack +20 if your character card costs 2 or more.

    If the opponent's character card costs 4 or more, the area enchant is removed
    (handled by check_area_enchant_removal).
    """
    player = game_state.players[player_index]
    if player.battle_zone is not None and player.battle_zone.card.power_cost >= 2:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: own character power cost %d >= 2, +20 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), player.battle_zone.card.power_cost, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: own character power cost %s < 2 or no character, no bonus', card_instance.card.effect, engine.player_label(player_index), player.battle_zone.card.power_cost if player.battle_zone else None)
