from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_103(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Select one of your opponent's Abyss cards and have it returned to the bottom of their deck."""
    opponent = game_state.players[1 - player_index]

    log.debug('[%s] %s: checking opponent abyss size=%d', card_instance.card.effect, engine.player_label(player_index), len(opponent.abyss))

    if not opponent.abyss:
        log.debug('[%s] %s: opponent abyss empty, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content="**Effect (01-103):** Opponent's Abyss is empty. Effect fizzles.",
        )
        return

    selected = await engine._prompt_card_selection(
        player_index,
        opponent.abyss,
        prompt_text="**Effect (01-103):** Choose a card from your opponent's Abyss to return to their deck.",
        placeholder='Select an Abyss card...',
    )

    if selected is None:
        log.debug('[%s] %s: no card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (01-103):** No effect.',
        )
        return

    # Move selected card from opponent's abyss to bottom of opponent's deck
    opponent.abyss.remove(selected)
    engine.return_to_deck_bottom(selected, opponent)
    log.debug('[%s] %s: moved opponent abyss card %s back to their deck', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)
