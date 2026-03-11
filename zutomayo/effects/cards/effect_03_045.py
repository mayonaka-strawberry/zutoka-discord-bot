from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.ui.embeds import build_hand_embed, create_deck_grid_image

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_045(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Reveal the opponent's hand."""
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if not opponent.hand:
        log.debug('[%s] %s: opponent hand is empty, skipping reveal', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content="**Effect (03-045):** Opponent's hand is empty.")
        return

    log.debug('[%s] %s: revealing opponent %s hand (%d cards)', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index), len(opponent.hand))

    embed = build_hand_embed(opponent)
    embed.title = "Opponent's Hand [相手の手札]"
    hand_card_names = ', '.join(c.card.name for c in opponent.hand)
    log.debug('[%s] %s: opponent hand contains: %s', card_instance.card.effect, engine.player_label(player_index), hand_card_names)

    reveal_img = create_deck_grid_image(opponent.hand, columns=len(opponent.hand))
    await engine._send_dm(player_index, content='**Effect (03-045):** Opponent\'s hand revealed!', embed=embed, file=reveal_img)

    reveal_img = create_deck_grid_image(opponent.hand, columns=len(opponent.hand))
    await engine._send_dm(opponent_index, content=f'**Effect (03-045):** Your hand has been revealed: {hand_card_names}.', file=reveal_img)

    reveal_img = create_deck_grid_image(opponent.hand, columns=len(opponent.hand))
    await engine._send_to_channel(content=f"**Effect (03-045):** Opponent's hand revealed: {hand_card_names}.", file=reveal_img)
    log.debug('[%s] %s: hand reveal complete', card_instance.card.effect, engine.player_label(player_index))
