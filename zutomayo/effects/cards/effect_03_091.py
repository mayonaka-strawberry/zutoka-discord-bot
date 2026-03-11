from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_091(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Attack +20 if your character card's power cost is 3 or more.

    When the opponent places a card in the Abyss, place this card on your
    Power Charger at the end of the turn (handled by check_area_enchant_removal).
    """
    player = game_state.players[player_index]
    has_battle = player.battle_zone is not None
    power_cost = player.battle_zone.card.power_cost if has_battle else None
    log.debug('[%s] %s: checking battle zone (has_battle=%s, power_cost=%s)', card_instance.card.effect, engine.player_label(player_index), has_battle, power_cost)
    if has_battle and power_cost >= 3:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: power cost %d >= 3, attack bonus +20 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), power_cost, old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: condition not met, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
