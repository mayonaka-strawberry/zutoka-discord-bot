from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_055(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Return the opponent's Area Enchantment card to the bottom of the deck.

    The opponent cannot set any Area Enchantment while this card is in play.
    When the opponent places a card in the Abyss, place this card on your
    Power Charger at the end of the turn.
    """
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Return opponent's area enchant to bottom of their deck
    if opponent.set_zone_c is not None:
        area_enchant = opponent.set_zone_c
        log.debug('[%s] %s: returning opponent %s area enchant %s to bottom of deck', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index), area_enchant.card.name)
        opponent.set_zone_c = None
        area_enchant.zone = Zone.DECK
        area_enchant.face_up = False
        opponent.deck.append(area_enchant)
        log.debug('[%s] %s: opponent area enchant moved to bottom of deck (deck size now %d)', card_instance.card.effect, engine.player_label(player_index), len(opponent.deck))
    else:
        log.debug('[%s] %s: opponent has no area enchantment to return', card_instance.card.effect, engine.player_label(player_index))

    # Block opponent from setting area enchants while this card is in play
    opponent.area_enchant_blocked = True
    log.debug('[%s] %s: blocking opponent %s area enchant placement', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index))
