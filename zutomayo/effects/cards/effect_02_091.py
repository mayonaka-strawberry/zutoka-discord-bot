from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_091(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Recovers 10 HP if your character card's attribute is flame."""
    player = game_state.players[player_index]
    if player.battle_zone is not None and player.battle_zone.effective_attribute == Attribute.FLAME:
        old_hp = player.hp
        engine.heal(game_state, player_index, 10, source=card_instance.card.effect)
        log.debug('[%s] %s: own character is Flame, +10 HP (from %d to %d)', card_instance.card.effect, engine.player_label(player_index), old_hp, player.hp)
    else:
        log.debug('[%s] %s: own character is not Flame, no HP recovery', card_instance.card.effect, engine.player_label(player_index))
