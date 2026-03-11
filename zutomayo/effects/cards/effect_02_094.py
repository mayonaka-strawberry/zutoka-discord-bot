from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_094(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Select a card from your hand, place it at the bottom of the deck and draw one more card."""
    log.debug('[%s] %s: selecting a card from hand to place at bottom of deck then draw 1', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]

    if not player.hand:
        log.debug('[%s] %s: hand is empty, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (02-094):** No cards in hand. Effect fizzles.',
        )
        return

    selected = await engine._prompt_card_selection(
        player_index,
        player.hand,
        prompt_text='**Effect (02-094):** Choose a card from your hand to place at the bottom of your deck.',
        placeholder='Select a hand card...',
    )

    if selected is None:
        log.debug('[%s] %s: no card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (02-094):** No effect.',
        )
        return

    log.debug('[%s] %s: selected card %s to place at bottom of deck', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)

    # Move selected card to bottom of deck
    player.hand.remove(selected)
    selected.zone = Zone.DECK
    selected.face_up = False
    player.deck.append(selected)

    # Draw one card
    if player.can_draw(1):
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)
        log.debug('[%s] %s: drew 1 card', card_instance.card.effect, engine.player_label(player_index))
    else:
        log.debug('[%s] %s: cannot draw (deck empty)', card_instance.card.effect, engine.player_label(player_index))
