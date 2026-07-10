from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import ReturnFromAbyssMessages, return_opponent_abyss_card_to_deck_bottom

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_04_090(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Return a card from your opponent's Abyss to the bottom of their deck."""
    await return_opponent_abyss_card_to_deck_bottom(
        engine, game_state, player_index, card_instance,
        messages=ReturnFromAbyssMessages(
            empty_abyss="**Effect (04-090):** Opponent's Abyss is empty. No effect.",
            prompt_text="**Effect (04-090):** Choose a card from the opponent's Abyss to return to the bottom of their deck.",
            placeholder="Select a card from opponent's Abyss...",
            no_selection='**Effect (04-090):** No card selected. No effect.',
            success_owner_template="**Effect (04-090):** Returned {name} to the bottom of opponent's deck.",
            success_opponent_template='**Effect (04-090):** Opponent returned {name} from your Abyss to the bottom of your deck.',
        ),
    )
