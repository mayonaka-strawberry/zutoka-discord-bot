from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional
from zutomayo.enums.card_type import CardType

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_006(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """This turn, reduce the power cost of your character cards set simultaneously by 2."""
    log.debug('[%s] %s: checking for character cards set this turn to reduce power cost by 2', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]

    # Find character card(s) set simultaneously this turn in set zones
    for zone_attr in ('set_zone_a', 'set_zone_b'):
        zone_ci: Optional[CardInstance] = getattr(player, zone_attr)
        if zone_ci is not None and zone_ci.played_this_turn and zone_ci.card.card_type == CardType.CHARACTER:
            zone_ci.power_cost_reduction = 2
            log.debug('[%s] %s: applied -2 power cost reduction to %s in %s', card_instance.card.effect, engine.player_label(player_index), zone_ci.card.effect, zone_attr)
        else:
            log.debug('[%s] %s: %s has no eligible character card this turn', card_instance.card.effect, engine.player_label(player_index), zone_attr)

    # Also apply to battle zone character if it was played this turn (already swapped)
    battle_zone_card = player.battle_zone
    if battle_zone_card is not None and battle_zone_card.played_this_turn and battle_zone_card.card.card_type == CardType.CHARACTER:
        battle_zone_card.power_cost_reduction = 2
        log.debug('[%s] %s: applied -2 power cost reduction to battle zone card %s', card_instance.card.effect, engine.player_label(player_index), battle_zone_card.card.effect)
    else:
        log.debug('[%s] %s: no eligible character card in battle zone this turn', card_instance.card.effect, engine.player_label(player_index))
