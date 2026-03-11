from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_090(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Return a card from your opponent's Abyss to the bottom of their deck."""
    log.debug('[%s] %s: entering effect_04_090', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if not opponent.abyss:
        await engine._send_dm(
            player_index,
            content="**Effect (04-090):** Opponent's Abyss is empty. No effect.",
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        opponent.abyss,
        prompt_text="**Effect (04-090):** Choose a card from the opponent's Abyss to return to the bottom of their deck.",
        placeholder="Select a card from opponent's Abyss...",
    )
    log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        await engine._send_dm(player_index, content='**Effect (04-090):** No card selected. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Move selected card from opponent's abyss to bottom of their deck
    opponent.abyss.remove(selected_card)
    selected_card.zone = Zone.DECK
    selected_card.face_up = False
    opponent.deck.append(selected_card)

    await engine._send_dm(
        player_index,
        content=f'**Effect (04-090):** Returned {selected_card.card.name} to the bottom of opponent\'s deck.',
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-090):** Opponent returned {selected_card.card.name} from your Abyss to the bottom of your deck.',
    )
