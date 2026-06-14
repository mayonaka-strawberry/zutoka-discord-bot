from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import CHRONOS_SIZE

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_026(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Time goes back by the opponent's clock minutes from the beginning of this turn."""
    opponent_index = 1 - player_index

    # The opponent's clock this turn is exactly what their cards advanced the
    # chronos by during the Advance Chronos phase. Use the snapshot taken at
    # advancement time rather than recounting zones: played cards have since
    # moved (area enchants to set zone C, blocked cards to power/abyss), and
    # the snapshot already reflects 02-005 clock-disable and 03-061
    # all-clocks-one as they applied when time actually advanced.
    total_opponent_clock = engine.turn_state.chronos_advanced.get(opponent_index, 0)

    log.debug('[%s] %s: opponent advanced chronos by %d this turn', card_instance.card.effect, engine.player_label(player_index), total_opponent_clock)
    if total_opponent_clock > 0:
        new_chronos = (game_state.chronos_at_turn_start - total_opponent_clock) % CHRONOS_SIZE
        log.debug('[%s] %s: rewinding chronos from %d to %d (turn_start=%d - opponent_clock=%d)', card_instance.card.effect, engine.player_label(player_index), game_state.chronos, new_chronos, game_state.chronos_at_turn_start, total_opponent_clock)
        engine.set_chronos(game_state, new_chronos)
    else:
        log.debug('[%s] %s: opponent clock is 0, no chronos change', card_instance.card.effect, engine.player_label(player_index))
