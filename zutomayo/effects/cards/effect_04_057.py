from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_057(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """If there are 3 or more cards in your Abyss, place the top 2 cards from your opponent's deck into their Abyss."""
    log.debug('[%s] %s: entering effect_04_057', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if len(player.abyss) < 3:
        await engine._send_dm(player_index, content=f'**Effect (04-057):** Only {len(player.abyss)} card(s) in Abyss. Need 3+. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    cards_to_move = min(2, len(opponent.deck))
    if cards_to_move == 0:
        await engine._send_dm(player_index, content="**Effect (04-057):** Opponent's deck is empty. No cards moved.")
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
        content=f"**Effect (04-057):** {cards_to_move} card(s) from opponent's deck placed in opponent's Abyss: {moved_names}.",
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-057):** {cards_to_move} card(s) from your deck placed in your Abyss: {moved_names}.',
    )
