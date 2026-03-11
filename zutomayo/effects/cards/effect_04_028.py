from __future__ import annotations
import random
from typing import TYPE_CHECKING
from constants import CHRONOS_SIZE
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_028(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Select 6 cards from the Abyss. Shuffle them face down and place them at the bottom of your deck.
    Otherwise, you lose the game.
    Set Chronos to your preferred time.
    """
    log.debug('[%s] %s: entering effect_04_028', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    # Phase 1: Check if player has at least 6 cards in Abyss
    if len(player.abyss) < 6:
        player.hp = 0
        log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-028):** Only {len(player.abyss)} card(s) in Abyss (need 6). You lose the game!',
        )
        await engine._send_dm(
            opponent_index,
            content=f'**Effect (04-028):** Opponent only has {len(player.abyss)} card(s) in Abyss (need 6). Opponent loses!',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Phase 2: Select 6 cards from Abyss
    remaining_abyss = list(player.abyss)
    selected_cards: list[CardInstance] = []

    for selection_number in range(1, 7):
        selected_card = await engine._prompt_card_selection(
            player_index,
            remaining_abyss,
            prompt_text=f'**Effect (04-028):** Choose card #{selection_number} of 6 from the Abyss to return to deck bottom.',
            placeholder='Select a card from Abyss...',
        )
        log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

        if selected_card is None:
            # Timeout — player loses the game (mandatory action)
            player.hp = 0
            log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
            await engine._send_dm(
                player_index,
                content='**Effect (04-028):** Failed to select 6 cards. You lose the game!',
            )
            await engine._send_dm(
                opponent_index,
                content='**Effect (04-028):** Opponent failed to select 6 cards from Abyss. Opponent loses!',
            )
            log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
            return

        selected_cards.append(selected_card)
        remaining_abyss.remove(selected_card)

    # Remove selected cards from abyss
    for selected_card in selected_cards:
        player.abyss.remove(selected_card)

    # Shuffle the 6 cards face down and place at the bottom of the deck
    random.shuffle(selected_cards)
    log.debug('[%s] %s: shuffled selected cards', card_instance.card.effect, engine.player_label(player_index))
    for selected_card in selected_cards:
        selected_card.zone = Zone.DECK
        selected_card.face_up = False
        player.deck.append(selected_card)

    returned_names = ', '.join(selected_card.card.name for selected_card in selected_cards)
    await engine._send_dm(
        player_index,
        content=f'**Effect (04-028):** Returned 6 cards to deck bottom (shuffled): {returned_names}.',
    )
    await engine._send_dm(
        opponent_index,
        content='**Effect (04-028):** Opponent returned 6 cards from Abyss to the bottom of their deck (shuffled).',
    )

    # Phase 3: Set Chronos to preferred time
    chosen_chronos = await engine._prompt_number_selection(
        player_index,
        min_value=0,
        max_value=CHRONOS_SIZE - 1,
        prompt_text='**Effect (04-028):** Choose your preferred Chronos time (0-17). Night: 0-8, Day: 9-17.',
        placeholder='Select Chronos value...',
    )
    log.debug('[%s] %s: number selection result: %s', card_instance.card.effect, engine.player_label(player_index), chosen_chronos)

    if chosen_chronos is not None:
        engine.set_chronos(game_state, chosen_chronos)
        log.debug('[%s] %s: chronos set to %d', card_instance.card.effect, engine.player_label(player_index), chosen_chronos)
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-028):** Chronos set to {chosen_chronos}.',
        )
        await engine._send_dm(
            opponent_index,
            content=f'**Effect (04-028):** Opponent set Chronos to {chosen_chronos}.',
        )
    else:
        await engine._send_dm(
            player_index,
            content='**Effect (04-028):** No Chronos selection made. Chronos unchanged.',
        )
