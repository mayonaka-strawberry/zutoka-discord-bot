from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.chronos import Chronos
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_101(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Attack +20 if the opponent's character's attack is 0."""
    log.debug('[%s] %s: entering effect_04_101', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Calculate opponent's effective attack power
    opponent_attack = 0
    if opponent.battle_zone is not None:
        effective_cost = engine.get_effective_power_cost(opponent.battle_zone, opponent)
        total_power = opponent.total_power + engine.turn_state.power_bonus.get(opponent_index, 0)

        if total_power >= effective_cost:
            opponent_card = opponent.battle_zone.card
            # Determine base attack considering day/night modifiers
            force_day = engine.should_force_day_attack(game_state, opponent_index)
            reversed_day_night = engine.should_reverse_day_night(game_state, opponent_index)

            if force_day:
                base_attack = opponent_card.attack_day
            elif reversed_day_night:
                if game_state.day_night == Chronos.NIGHT:
                    base_attack = opponent_card.attack_day
                else:
                    base_attack = opponent_card.attack_night
            else:
                if game_state.day_night == Chronos.NIGHT:
                    base_attack = opponent_card.attack_night
                else:
                    base_attack = opponent_card.attack_day

            modifier = engine.turn_state.attack_bonus.get(opponent_index, 0)
            opponent_attack = max(0, base_attack + modifier)

    if opponent_attack == 0:
        engine.turn_state.attack_bonus[player_index] += 20
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 20, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content="**Effect (04-101):** Opponent's character attack is 0. Attack +20!")
        await engine._send_dm(opponent_index, content='**Effect (04-101):** Your character attack is 0. Opponent gains Attack +20.')
    else:
        await engine._send_dm(player_index, content=f"**Effect (04-101):** Opponent's character attack is {opponent_attack}. No effect.")
