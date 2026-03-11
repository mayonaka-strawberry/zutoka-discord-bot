from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.ui.embeds import create_deck_grid_image
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_097(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Reveal your hand. Attack +50 if it contains 3 or more attributes."""
    log.debug('[%s] %s: entering effect_04_097', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if not player.hand:
        await engine._send_dm(player_index, content='**Effect (04-097):** Hand is empty. No attribute bonus.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    hand_attributes = {hand_card.card.attribute for hand_card in player.hand}
    hand_card_names = ', '.join(hand_card.card.name for hand_card in player.hand)

    reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
    await engine._send_dm(player_index, content='**Effect (04-097):** Your revealed hand:', file=reveal_img)

    reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
    await engine._send_dm(opponent_index, content=f'**Effect (04-097):** Opponent reveals hand: {hand_card_names}.', file=reveal_img)

    reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
    await engine._send_to_channel(content=f'**Effect (04-097):** Opponent reveals hand: {hand_card_names}.', file=reveal_img)

    if len(hand_attributes) >= 3:
        engine.turn_state.attack_bonus[player_index] += 50
        log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 50, engine.turn_state.attack_bonus[player_index])
        attribute_names = ', '.join(attribute.value for attribute in hand_attributes)
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-097):** Hand revealed with {len(hand_attributes)} attributes ({attribute_names}). Attack +50!',
        )
        await engine._send_dm(
            opponent_index,
            content=f'**Effect (04-097):** Opponent has {len(hand_attributes)} attributes in hand. Attack +50.',
        )
    else:
        attribute_names = ', '.join(attribute.value for attribute in hand_attributes)
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-097):** Hand revealed with only {len(hand_attributes)} attribute(s) ({attribute_names}). Need 3+. No bonus.',
        )
