from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_027(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Select two cards from your hand, place them at the bottom of the deck and draw two cards."""
    log.debug('[%s] %s: selecting 2 cards from hand to place at bottom of deck then draw 2', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]

    if len(player.hand) < 2:
        log.debug('[%s] %s: hand has %d cards (need 2), effect fizzles', card_instance.card.effect, engine.player_label(player_index), len(player.hand))
        await engine._send_dm(
            player_index,
            content='**Effect (02-027):** Not enough cards in hand. Effect fizzles.',
        )
        return

    # Select first card
    first = await engine._prompt_card_selection(
        player_index,
        list(player.hand),
        prompt_text='**Effect (02-027):** Choose the first card from your hand to place at the bottom of your deck.',
        placeholder='Select first card...',
    )

    if first is None:
        log.debug('[%s] %s: no first card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (02-027):** No effect.')
        return

    log.debug('[%s] %s: first card selected: %s', card_instance.card.effect, engine.player_label(player_index), first.card.effect)

    # Select second card (exclude first pick)
    remaining = [card_instance for card_instance in player.hand if card_instance is not first]
    if not remaining:
        log.debug('[%s] %s: no second card available, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (02-027):** No second card available. No effect.')
        return

    second = await engine._prompt_card_selection(
        player_index,
        remaining,
        prompt_text='**Effect (02-027):** Choose the second card from your hand to place at the bottom of your deck.',
        placeholder='Select second card...',
    )

    if second is None:
        log.debug('[%s] %s: no second card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (02-027):** No effect.')
        return

    log.debug('[%s] %s: second card selected: %s', card_instance.card.effect, engine.player_label(player_index), second.card.effect)

    # Move both cards from hand to bottom of deck
    for card in (first, second):
        if card in player.hand:
            player.hand.remove(card)
        card.zone = Zone.DECK
        card.face_up = False
        player.deck.append(card)

    log.debug('[%s] %s: moved 2 cards to bottom of deck', card_instance.card.effect, engine.player_label(player_index))

    # Draw two cards
    if player.can_draw(2):
        player.draw(2)
        await engine.notify_draw(game_state, player_index, 2)
        log.debug('[%s] %s: drew 2 cards', card_instance.card.effect, engine.player_label(player_index))
    else:
        log.debug('[%s] %s: cannot draw 2 cards (deck too small)', card_instance.card.effect, engine.player_label(player_index))
