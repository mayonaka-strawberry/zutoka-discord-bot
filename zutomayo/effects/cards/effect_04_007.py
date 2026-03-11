from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.card_type import CardType
from zutomayo.enums.song import Song
from zutomayo.ui.embeds import create_deck_grid_image
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_007(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Reveal any number of (TAIDADA) characters from your hand, Attack +20 for each."""
    log.debug('[%s] %s: entering effect_04_007', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]

    taidada_characters = [
        hand_card for hand_card in player.hand
        if hand_card.card.song == Song.TAIDADA
        and hand_card.card.card_type == CardType.CHARACTER
        and hand_card.unique_id != card_instance.unique_id
    ]

    if not taidada_characters:
        await engine._send_dm(player_index, content='**Effect (04-007):** No TAIDADA characters in hand. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    reveal_count = await engine._prompt_number_selection(
        player_index,
        min_value=0,
        max_value=len(taidada_characters),
        prompt_text=f'**Effect (04-007):** You have {len(taidada_characters)} TAIDADA character(s) in hand. How many do you want to reveal?',
        placeholder='Select number to reveal...',
    )
    log.debug('[%s] %s: number selection result: %s', card_instance.card.effect, engine.player_label(player_index), reveal_count)

    if not reveal_count:
        await engine._send_dm(player_index, content='**Effect (04-007):** No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    attack_bonus = 20 * reveal_count
    engine.turn_state.attack_bonus[player_index] += attack_bonus
    log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), attack_bonus, engine.turn_state.attack_bonus[player_index])

    revealed_cards = taidada_characters[:reveal_count]
    revealed_names = ', '.join(hand_card.card.name for hand_card in revealed_cards)
    player_msg = f'**Effect (04-007):** Revealed {reveal_count} TAIDADA character(s): {revealed_names}. Attack +{attack_bonus}!'
    opponent_msg = f'**Effect (04-007):** Opponent revealed {reveal_count} TAIDADA character(s): {revealed_names}.'

    # Send text + reveal image to both players and the server channel.
    # discord.File is consumed on send, so we create a fresh image per recipient.
    reveal_img = create_deck_grid_image(revealed_cards, columns=reveal_count)
    await engine._send_dm(player_index, content=player_msg, file=reveal_img)

    reveal_img = create_deck_grid_image(revealed_cards, columns=reveal_count)
    await engine._send_dm(1 - player_index, content=opponent_msg, file=reveal_img)

    reveal_img = create_deck_grid_image(revealed_cards, columns=reveal_count)
    await engine._send_to_channel(content=opponent_msg, file=reveal_img)
