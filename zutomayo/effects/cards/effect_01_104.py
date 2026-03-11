from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_104(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Put the top card of the opponent's deck on the Abyss (regardless of power)."""
    opponent = game_state.players[1 - player_index]

    if not opponent.deck:
        log.debug('[%s] %s: opponent deck is empty, no effect', card_instance.card.effect, engine.player_label(player_index))
        return

    top_card = opponent.deck.pop(0)
    top_card.zone = Zone.ABYSS
    top_card.face_up = True
    opponent.abyss.append(top_card)
    log.debug('[%s] %s: moved top card %s from opponent deck to their abyss', card_instance.card.effect, engine.player_label(player_index), top_card.card.effect)
