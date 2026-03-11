from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_027(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Recovers 50 HP to the opponent and inflicts 50 damage at the end of the turn."""
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Immediately recover 50 HP to opponent (capped at 100)
    old_hp = opponent.hp
    opponent.hp = min(opponent.hp + 50, 100)
    log.debug('[%s] %s: healed opponent %s by 50 (HP %d -> %d)', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index), old_hp, opponent.hp)

    # Schedule 50 damage to opponent at end of turn
    engine.turn_state.end_of_turn_damage[opponent_index] += 50
    log.debug('[%s] %s: scheduled 50 end-of-turn damage to %s', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index))
