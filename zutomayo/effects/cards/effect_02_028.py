from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_028(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If your character card and your opponent's character card have the same Power cost, attack +40."""
    player = game_state.players[player_index]
    opponent = game_state.players[1 - player_index]

    if (
        player.battle_zone is not None
        and opponent.battle_zone is not None
        and player.battle_zone.card.power_cost == opponent.battle_zone.card.power_cost
    ):
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: same power cost (%d), +40 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), player.battle_zone.card.power_cost, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: power costs differ (own=%s, opp=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), player.battle_zone.card.power_cost if player.battle_zone else None, opponent.battle_zone.card.power_cost if opponent.battle_zone else None)
