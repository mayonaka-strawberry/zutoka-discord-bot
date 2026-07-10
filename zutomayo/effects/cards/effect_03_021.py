from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_021(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Put the opponent's Area Enchantment card on top of the opponent's deck."""
    opponent = game_state.players[1 - player_index]

    if opponent.set_zone_c is None:
        log.debug('[%s] %s: opponent has no area enchantment, skipping', card_instance.card.effect, engine.player_label(player_index))
        return

    area_enchant = opponent.set_zone_c
    log.debug('[%s] %s: returning opponent area enchant %s to top of deck', card_instance.card.effect, engine.player_label(player_index), area_enchant.card.name)
    opponent.set_zone_c = None
    engine.return_to_deck_top(area_enchant, opponent)
    engine.on_area_enchant_leaves_play(game_state, area_enchant, 1 - player_index)
    log.debug('[%s] %s: opponent area enchant moved to top of deck (deck size now %d)', card_instance.card.effect, engine.player_label(player_index), len(opponent.deck))
