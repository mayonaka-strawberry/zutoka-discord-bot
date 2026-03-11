from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_062(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Place any number of darkness cards from your hand into the Abyss. If you do, draw the same number of cards."""
    log.debug('[%s] %s: entering effect_04_062', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    darkness_cards = [
        hand_card for hand_card in player.hand
        if hand_card.card.attribute == Attribute.DARKNESS
    ]

    if not darkness_cards:
        await engine._send_dm(player_index, content='**Effect (04-062):** No darkness cards in hand. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selection_count = await engine._prompt_number_selection(
        player_index,
        min_value=0,
        max_value=len(darkness_cards),
        prompt_text=f'**Effect (04-062):** You have {len(darkness_cards)} darkness card(s) in hand. How many do you want to place into the Abyss?',
        placeholder='Select number of cards...',
    )
    log.debug('[%s] %s: number selection result: %s', card_instance.card.effect, engine.player_label(player_index), selection_count)

    if not selection_count:
        await engine._send_dm(player_index, content='**Effect (04-062):** No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Select cards individually
    remaining_candidates = list(darkness_cards)
    selected_cards: list[CardInstance] = []

    for selection_number in range(1, selection_count + 1):
        selected_card = await engine._prompt_card_selection(
            player_index,
            remaining_candidates,
            prompt_text=f'**Effect (04-062):** Choose darkness card #{selection_number} of {selection_count} to place into the Abyss.',
            placeholder='Select a darkness card...',
        )
        log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

        if selected_card is None:
            break

        selected_cards.append(selected_card)
        remaining_candidates.remove(selected_card)

    if not selected_cards:
        await engine._send_dm(player_index, content='**Effect (04-062):** No cards selected. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Move selected cards from hand to abyss
    for selected_card in selected_cards:
        player.hand.remove(selected_card)
        selected_card.zone = Zone.ABYSS
        selected_card.face_up = True
        player.abyss.append(selected_card)

    placed_names = ', '.join(selected_card.card.name for selected_card in selected_cards)
    await engine._send_dm(player_index, content=f'**Effect (04-062):** Placed {len(selected_cards)} card(s) into the Abyss: {placed_names}.')
    await engine._send_dm(opponent_index, content=f'**Effect (04-062):** Opponent placed {len(selected_cards)} darkness card(s) into their Abyss.')

    # Draw the same number of cards
    draw_count = min(len(selected_cards), len(player.deck))
    if draw_count > 0:
        log.debug('[%s] %s: drawing %s card(s)', card_instance.card.effect, engine.player_label(player_index), draw_count)
        player.draw(draw_count)
        await engine.notify_draw(game_state, player_index, draw_count)
