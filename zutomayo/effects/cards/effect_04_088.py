from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_088(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Select a card from the Abyss. Shuffle them face down and place them at the bottom of your deck.
    Otherwise, you lose the game.
    Look at the top 3 cards of your opponent's deck, rearrange them in any order you like, and return them to the deck.
    """
    log.debug('[%s] %s: entering effect_04_088', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Phase 1: Select a card from Abyss and place at deck bottom (mandatory)
    if not player.abyss:
        player.hp = 0
        log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (04-088):** No cards in Abyss. You lose the game!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-088):** Opponent has no cards in Abyss. Opponent loses!',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        player.abyss,
        prompt_text='**Effect (04-088):** Choose a card from the Abyss to place at the bottom of your deck (mandatory).',
        placeholder='Select a card from Abyss...',
    )
    log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        # Timeout — player loses the game (mandatory action)
        player.hp = 0
        log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (04-088):** Failed to select a card. You lose the game!',
        )
        await engine._send_dm(
            opponent_index,
            content='**Effect (04-088):** Opponent failed to select a card from Abyss. Opponent loses!',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Move selected card from abyss to deck bottom face down
    player.abyss.remove(selected_card)
    selected_card.zone = Zone.DECK
    selected_card.face_up = False
    player.deck.append(selected_card)

    await engine._send_dm(
        player_index,
        content=f'**Effect (04-088):** Placed {selected_card.card.name} at the bottom of your deck.',
    )
    await engine._send_dm(
        opponent_index,
        content='**Effect (04-088):** Opponent returned a card from Abyss to the bottom of their deck.',
    )

    # Phase 2: Look at top 3 cards of opponent's deck and rearrange
    cards_to_view = min(3, len(opponent.deck))
    if cards_to_view == 0:
        await engine._send_dm(player_index, content="**Effect (04-088):** Opponent's deck is empty. Cannot rearrange.")
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    top_cards = opponent.deck[:cards_to_view]
    card_names = ', '.join(top_card.card.name for top_card in top_cards)
    await engine._send_dm(
        player_index,
        content=f"**Effect (04-088):** Top {cards_to_view} card(s) of opponent's deck: {card_names}. Choose the new order.",
    )

    if cards_to_view == 1:
        # Only 1 card, no rearranging needed
        await engine._send_dm(player_index, content="**Effect (04-088):** Only 1 card — no rearranging needed.")
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Let the player pick the order by selecting cards one at a time
    remaining_cards = list(top_cards)
    reordered_cards: list[CardInstance] = []

    for position in range(1, cards_to_view + 1):
        if len(remaining_cards) == 1:
            # Last card goes automatically
            reordered_cards.append(remaining_cards[0])
            break

        selected_position_card = await engine._prompt_card_selection(
            player_index,
            remaining_cards,
            prompt_text=f'**Effect (04-088):** Choose the card to place at position #{position} from the top.',
            placeholder=f'Select card for position #{position}...',
        )
        log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_position_card.card.name if selected_position_card else None)

        if selected_position_card is None:
            # Timeout — keep remaining cards in current order
            reordered_cards.extend(remaining_cards)
            break

        reordered_cards.append(selected_position_card)
        remaining_cards.remove(selected_position_card)

    # Replace top cards of opponent's deck with the reordered cards
    opponent.deck[:cards_to_view] = reordered_cards
    log.debug('[%s] %s: rearranged top %d cards of opponent deck', card_instance.card.effect, engine.player_label(player_index), cards_to_view)

    reordered_names = ', '.join(reordered_card.card.name for reordered_card in reordered_cards)
    await engine._send_dm(
        player_index,
        content=f"**Effect (04-088):** Rearranged opponent's top cards: {reordered_names}.",
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-088):** Opponent rearranged the top {cards_to_view} card(s) of your deck.',
    )
