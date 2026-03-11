from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_050(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If the Abyss has an electric card, reduce the opponent's HP by 20."""
    player = game_state.players[player_index]
    opponent = game_state.players[1 - player_index]
    if any(card_instance.card.attribute == Attribute.ELECTRICITY for card_instance in player.abyss):
        old_hp = opponent.hp
        opponent.hp = max(0, opponent.hp - 20)
        log.debug('[%s] %s: electric card in abyss, opponent HP -%d (from %d to %d)', card_instance.card.effect, engine.player_label(player_index), old_hp - opponent.hp, old_hp, opponent.hp)
    else:
        log.debug('[%s] %s: no electric card in abyss, no HP reduction', card_instance.card.effect, engine.player_label(player_index))
