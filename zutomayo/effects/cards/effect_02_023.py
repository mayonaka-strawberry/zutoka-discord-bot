from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_023(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Attack +40 if the attribute of the character card used in the previous turn is electric
    and the opponent's character card is flame or darkness.
    """
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is None or prev_char.attribute != Attribute.ELECTRICITY:
        log.debug('[%s] %s: previous character was not Electric (prev_char=%s), skipping', card_instance.card.effect, engine.player_label(player_index), prev_char.attribute if prev_char else None)
        return

    opponent = game_state.players[1 - player_index]
    if opponent.battle_zone is not None and opponent.battle_zone.effective_attribute in (Attribute.FLAME, Attribute.DARKNESS):
        engine.turn_state.attack_bonus[player_index] += 40
        log.debug('[%s] %s: prev Electric + opponent %s, +40 attack bonus (now %d)', card_instance.card.effect, engine.player_label(player_index), opponent.battle_zone.effective_attribute, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: prev Electric but opponent is not Flame/Darkness, no bonus', card_instance.card.effect, engine.player_label(player_index))
