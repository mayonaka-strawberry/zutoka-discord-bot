from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import ReturnFromAbyssMessages, return_opponent_abyss_card_to_deck_bottom

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_01_103(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Select one of your opponent's Abyss cards and have it returned to the bottom of their deck."""
    await return_opponent_abyss_card_to_deck_bottom(
        engine, game_state, player_index, card_instance,
        messages=ReturnFromAbyssMessages(
            empty_abyss="**Effect (01-103):** Opponent's Abyss is empty. Effect fizzles.",
            prompt_text="**Effect (01-103):** Choose a card from your opponent's Abyss to return to their deck.",
            placeholder='Select an Abyss card...',
            no_selection='**Effect (01-103):** No effect.',
            success_owner_template=None,
            success_opponent_template=None,
        ),
    )
