from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_064(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    + Attack by the amount of your own HP each other.

    Both players receive an attack bonus equal to their own HP.
    Removal condition (opponent HP <= 40) is handled in check_area_enchant_removal().
    """
    opponent_index = 1 - player_index
    player_hp = game_state.players[player_index].hp
    opponent_hp = game_state.players[opponent_index].hp

    old_bonus_p = engine.turn_state.attack_bonus[player_index]
    engine.turn_state.attack_bonus[player_index] += player_hp
    log.debug('[%s] %s: attack bonus +%d (own HP) (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), player_hp, old_bonus_p, engine.turn_state.attack_bonus[player_index])

    old_bonus_o = engine.turn_state.attack_bonus[opponent_index]
    engine.turn_state.attack_bonus[opponent_index] += opponent_hp
    log.debug('[%s] %s: opponent %s attack bonus +%d (own HP) (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index), opponent_hp, old_bonus_o, engine.turn_state.attack_bonus[opponent_index])
