from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import MIDNIGHT

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_005(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """If your HP is less than opponent's HP, set the Clock to midnight."""
    player = game_state.players[player_index]
    opponent = game_state.players[1 - player_index]
    log.debug('[%s] %s: checking HP comparison (player HP=%d, opponent HP=%d)', card_instance.card.effect, engine.player_label(player_index), player.hp, opponent.hp)
    if player.hp < opponent.hp:
        log.debug('[%s] %s: player HP < opponent HP, setting clock to midnight (%d)', card_instance.card.effect, engine.player_label(player_index), MIDNIGHT)
        engine.set_chronos(game_state, MIDNIGHT)
    else:
        log.debug('[%s] %s: player HP >= opponent HP, no clock change', card_instance.card.effect, engine.player_label(player_index))
