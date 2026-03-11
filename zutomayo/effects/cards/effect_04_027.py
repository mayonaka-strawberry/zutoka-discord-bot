from __future__ import annotations
import random
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_027(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Select 1 or more cards from the Abyss. Shuffle them face down and place them at the bottom
    of your deck. Otherwise, you lose the game.
    Then, place the same number of cards from the top of your opponent's deck into the Abyss.
    """
    log.debug('[%s] %s: entering effect_04_027', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Phase 1: Check if player has at least 1 card in Abyss
    if len(player.abyss) < 1:
        player.hp = 0
        log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (04-027):** No cards in Abyss. You lose the game!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-027):** Opponent has no cards in Abyss. Opponent loses!',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Phase 2: Prompt for how many cards to select
    selection_count = await engine._prompt_number_selection(
        player_index,
        min_value=1,
        max_value=len(player.abyss),
        prompt_text=f'**Effect (04-027):** You have {len(player.abyss)} card(s) in Abyss. How many do you want to return to deck bottom?',
        placeholder='Select number of cards...',
    )
    log.debug('[%s] %s: number selection result: %s', card_instance.card.effect, engine.player_label(player_index), selection_count)

    if selection_count is None:
        # Timeout — player loses the game (mandatory action)
        player.hp = 0
        log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (04-027):** Failed to select. You lose the game!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-027):** Opponent failed to select cards from Abyss. Opponent loses!',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Phase 3: Select cards individually from Abyss
    remaining_abyss = list(player.abyss)
    selected_cards: list[CardInstance] = []

    for selection_number in range(1, selection_count + 1):
        selected_card = await engine._prompt_card_selection(
            player_index,
            remaining_abyss,
            prompt_text=f'**Effect (04-027):** Choose card #{selection_number} of {selection_count} from the Abyss to return to deck bottom.',
            placeholder='Select a card from Abyss...',
        )
        log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

        if selected_card is None:
            # Timeout — player loses the game (mandatory action)
            player.hp = 0
            log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
            await engine._send_dm(
                player_index,
                content='**Effect (04-027):** Failed to select cards. You lose the game!',
            )
            await engine._send_dm(
                opponent_index,
                content='**Effect (04-027):** Opponent failed to select cards from Abyss. Opponent loses!',
            )
            log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
            return

        selected_cards.append(selected_card)
        remaining_abyss.remove(selected_card)

    # Remove selected cards from abyss
    for selected_card in selected_cards:
        player.abyss.remove(selected_card)

    # Shuffle the selected cards face down and place at the bottom of the deck
    random.shuffle(selected_cards)
    log.debug('[%s] %s: shuffled selected cards', card_instance.card.effect, engine.player_label(player_index))
    for selected_card in selected_cards:
        selected_card.zone = Zone.DECK
        selected_card.face_up = False
        player.deck.append(selected_card)

    returned_names = ', '.join(selected_card.card.name for selected_card in selected_cards)
    await engine._send_dm(
        player_index,
        content=f'**Effect (04-027):** Returned {selection_count} card(s) to deck bottom (shuffled): {returned_names}.',
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-027):** Opponent returned {selection_count} card(s) from Abyss to the bottom of their deck (shuffled).',
    )

    # Phase 4: Place the same number of cards from opponent's deck top into opponent's Abyss
    cards_to_move = min(selection_count, len(opponent.deck))
    if cards_to_move == 0:
        await engine._send_dm(
            player_index,
            content="**Effect (04-027):** Opponent's deck is empty. No cards moved to Abyss.",
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    moved_cards: list[CardInstance] = []
    for _ in range(cards_to_move):
        deck_card = opponent.deck.pop(0)
        deck_card.zone = Zone.ABYSS
        deck_card.face_up = True
        opponent.abyss.append(deck_card)
        moved_cards.append(deck_card)

    moved_names = ', '.join(moved_card.card.name for moved_card in moved_cards)
    await engine._send_dm(
        player_index,
        content=f"**Effect (04-027):** {cards_to_move} card(s) from opponent's deck placed in opponent's Abyss: {moved_names}.",
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-027):** {cards_to_move} card(s) from your deck placed in your Abyss: {moved_names}.',
    )
