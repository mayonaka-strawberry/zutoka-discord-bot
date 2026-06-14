from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_041(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    If the attribute of the character card used in the previous turn is darkness,
    place the top card of the deck on the Power charger if it has power, otherwise on the Abyss.
    """
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is None or prev_char.attribute != Attribute.DARKNESS:
        log.debug('[%s] %s: previous character was not Darkness (prev_char=%s), skipping', card_instance.card.effect, engine.player_label(player_index), prev_char.attribute if prev_char else None)
        return

    player = game_state.players[player_index]
    if not player.deck:
        log.debug('[%s] %s: deck is empty, cannot place top card', card_instance.card.effect, engine.player_label(player_index))
        return

    top_card = player.deck.pop(0)
    if top_card.card.send_to_power > 0:
        engine.place_in_power_charger(top_card, player, player_index)
        log.debug('[%s] %s: top card %s has STP=%d, placed on power charger', card_instance.card.effect, engine.player_label(player_index), top_card.card.effect, top_card.card.send_to_power)
    else:
        engine.place_in_abyss(top_card, player, player_index)
        log.debug('[%s] %s: top card %s has no power, placed on abyss', card_instance.card.effect, engine.player_label(player_index), top_card.card.effect)
