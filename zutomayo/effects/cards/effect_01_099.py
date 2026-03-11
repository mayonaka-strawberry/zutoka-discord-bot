from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_099(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Recovers 10 HP if your character card's attribute is wind."""
    player = game_state.players[player_index]
    if player.battle_zone is not None and player.battle_zone.effective_attribute == Attribute.WIND:
        old_hp = player.hp
        player.hp = min(player.hp + 10, 100)
        log.debug('[%s] %s: own attribute is WIND, recovered 10 HP (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_hp, player.hp)
    else:
        log.debug('[%s] %s: own attribute is not WIND (battle_zone=%s), no recovery', card_instance.card.effect, engine.player_label(player_index), player.battle_zone.effective_attribute if player.battle_zone else None)
