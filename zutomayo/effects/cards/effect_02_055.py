from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_055(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    If your character card's attribute is flame, place your opponent's area enchantment card
    on top of your opponent's deck.
    """
    player = game_state.players[player_index]
    opponent = game_state.players[1 - player_index]

    if player.battle_zone is None or player.battle_zone.effective_attribute != Attribute.FLAME:
        log.debug('[%s] %s: own character is not Flame, skipping', card_instance.card.effect, engine.player_label(player_index))
        return

    if opponent.set_zone_c is None:
        log.debug('[%s] %s: opponent has no area enchantment, skipping', card_instance.card.effect, engine.player_label(player_index))
        return

    area_enchant = opponent.set_zone_c
    opponent.set_zone_c = None
    area_enchant.zone = Zone.DECK
    area_enchant.face_up = False
    opponent.deck.insert(0, area_enchant)
    log.debug('[%s] %s: moved opponent area enchantment %s to top of opponent deck', card_instance.card.effect, engine.player_label(player_index), area_enchant.card.effect)
