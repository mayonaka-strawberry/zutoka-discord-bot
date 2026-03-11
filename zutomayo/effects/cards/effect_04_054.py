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


async def effect_04_054(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Place an electric card from your hand into the Abyss. If you do, draw 1 card."""
    log.debug('[%s] %s: entering effect_04_054', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    electric_cards = [
        hand_card for hand_card in player.hand
        if hand_card.card.attribute == Attribute.ELECTRICITY
    ]

    if not electric_cards:
        await engine._send_dm(player_index, content='**Effect (04-054):** No electric cards in hand. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        electric_cards,
        prompt_text='**Effect (04-054):** Choose an electric card from your hand to place into the Abyss.',
        placeholder='Select an electric card...',
    )
    log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        await engine._send_dm(player_index, content='**Effect (04-054):** No card selected. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Move selected card from hand to abyss
    player.hand.remove(selected_card)
    selected_card.zone = Zone.ABYSS
    selected_card.face_up = True
    player.abyss.append(selected_card)

    await engine._send_dm(player_index, content=f'**Effect (04-054):** Placed {selected_card.card.name} into the Abyss.')
    await engine._send_dm(opponent_index, content=f'**Effect (04-054):** Opponent placed {selected_card.card.name} into their Abyss.')

    # Draw 1 card
    if player.can_draw(1):
        log.debug('[%s] %s: drawing %s card(s)', card_instance.card.effect, engine.player_label(player_index), 1)
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)
