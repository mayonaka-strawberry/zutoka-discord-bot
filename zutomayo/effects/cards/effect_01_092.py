from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_092(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Draw a card from the deck and add it to your hand.
    
    The extra card permanently increases the player's hand size by one
    because end-of-turn draw replaces only the cards played, not the bonus draw.
    """
    player = game_state.players[player_index]
    if player.can_draw(1):
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)
        player.pending_hand_size_bonus += 1
        log.debug('[%s] %s: drew 1 card, pending_hand_size_bonus now %d', card_instance.card.effect, engine.player_label(player_index), player.pending_hand_size_bonus)
    else:
        log.debug('[%s] %s: cannot draw (deck empty), no effect', card_instance.card.effect, engine.player_label(player_index))
