from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import CHRONOS_SIZE
from zutomayo.enums.card_type import CardType

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_026(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Time goes back by the opponent's clock minutes from the beginning of this turn."""
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Gather opponent's cards played this turn (mirrors get_cards_played_this_turn)
    opponent_cards = []
    if opponent.set_zone_a and opponent.set_zone_a.played_this_turn:
        opponent_cards.append(opponent.set_zone_a)
    if opponent.set_zone_b and opponent.set_zone_b.played_this_turn:
        opponent_cards.append(opponent.set_zone_b)
    if opponent.battle_zone and opponent.battle_zone.played_this_turn:
        opponent_cards.append(opponent.battle_zone)

    # Calculate total clock, respecting clock-disable effects
    clock_disabled = engine.is_opponent_clock_disabled(game_state, opponent_index)
    total_opponent_clock = 0
    for card_inst in opponent_cards:
        if clock_disabled and card_inst.card.card_type == CardType.CHARACTER:
            continue
        total_opponent_clock += card_inst.card.clock

    log.debug('[%s] %s: opponent played %d cards this turn, clock_disabled=%s, total_opponent_clock=%d', card_instance.card.effect, engine.player_label(player_index), len(opponent_cards), clock_disabled, total_opponent_clock)
    if total_opponent_clock > 0:
        new_chronos = (game_state.chronos_at_turn_start - total_opponent_clock) % CHRONOS_SIZE
        log.debug('[%s] %s: rewinding chronos from %d to %d (turn_start=%d - opponent_clock=%d)', card_instance.card.effect, engine.player_label(player_index), game_state.chronos, new_chronos, game_state.chronos_at_turn_start, total_opponent_clock)
        engine.set_chronos(game_state, new_chronos)
    else:
        log.debug('[%s] %s: opponent clock is 0, no chronos change', card_instance.card.effect, engine.player_label(player_index))
