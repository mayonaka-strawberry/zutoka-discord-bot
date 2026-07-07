from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.ui.embeds import create_deck_grid_image
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_032(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Reveal your hand. Attack +50 if it contains 4 or more attributes.
    If there is an Area Enchantment on your opponent's side, this card is
    immediately placed into the Abyss.

    Note: the printed effect text says "immediately place it into the Abyss",
    where "it" refers to THIS card, not the opponent's Area Enchantment
    (per the Japanese text, same self-removal pattern as 03-061).
    Ruling re-confirmed as intended on 2026-06-12, including the timing where
    the immediate removal can fire before this card's reveal/+50 resolves.

    Self-removal is handled in check_area_enchant_removal().
    """
    log.debug('[%s] %s: entering effect_04_032', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    # Part 1: Reveal hand and check attributes
    if not player.hand:
        await engine._send_dm(player_index, content='**Effect (04-032):** Hand is empty. No attribute bonus.')
    else:
        hand_attributes = {hand_card.card.attribute for hand_card in player.hand}
        hand_card_names = ', '.join(hand_card.card.name for hand_card in player.hand)

        reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
        await engine._send_dm(player_index, content='**Effect (04-032):** Your revealed hand:', file=reveal_img)

        reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
        await engine._send_dm(opponent_index, content=f'**Effect (04-032):** Opponent reveals hand: {hand_card_names}.', file=reveal_img)

        reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
        await engine._send_to_channel(content=f'**Effect (04-032):** Opponent reveals hand: {hand_card_names}.', file=reveal_img)

        if len(hand_attributes) >= 4:
            engine.turn_state.attack_bonus[player_index] += 50
            log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 50, engine.turn_state.attack_bonus[player_index])
            attribute_names = ', '.join(sorted(attribute.value for attribute in hand_attributes))
            await engine._send_dm(player_index, content=f'**Effect (04-032):** Hand revealed with {len(hand_attributes)} attributes ({attribute_names}). Attack +50!')
            await engine._send_dm(opponent_index, content=f'**Effect (04-032):** Opponent has {len(hand_attributes)} attributes in hand. Attack +50.')
        else:
            attribute_names = ', '.join(sorted(attribute.value for attribute in hand_attributes))
            await engine._send_dm(player_index, content=f'**Effect (04-032):** Hand revealed with only {len(hand_attributes)} attribute(s) ({attribute_names}). Need 4+. No bonus.')
