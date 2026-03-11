from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_081(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if your character card's attribute is flame."""
    player = game_state.players[player_index]
    has_battle = player.battle_zone is not None
    attr = player.battle_zone.effective_attribute if has_battle else None
    log.debug('[%s] %s: checking battle zone (has_battle=%s, attribute=%s)', card_instance.card.effect, engine.player_label(player_index), has_battle, attr)
    if has_battle and attr == Attribute.FLAME:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: character is flame, attack bonus +20 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
    else:
        log.debug('[%s] %s: character not flame, no bonus applied', card_instance.card.effect, engine.player_label(player_index))
