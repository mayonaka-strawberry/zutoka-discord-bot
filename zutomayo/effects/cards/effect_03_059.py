from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.effects.cards._card_id_validator import validate_card_id
from zutomayo.ui.embeds import card_detail_description, create_deck_grid_image

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_059(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Specify the name of a card. Choose and reveal 1 card from the opponent's hand. If it matches, Attack +100."""
    opponent = game_state.players[1 - player_index]

    if not opponent.hand:
        log.debug('[%s] %s: opponent hand is empty, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (03-059):** No effect.')
        return

    # Step 1: Player types a card ID (XX-XXX format)
    log.debug('[%s] %s: prompting for card ID guess', card_instance.card.effect, engine.player_label(player_index))
    specified_id = await engine._prompt_text_input(
        player_index,
        prompt_text='**Effect (03-059):** Enter a card ID (XX-XXX) to guess.',
        modal_title='Specify a card',
        validator=validate_card_id,
    )

    if specified_id is None:
        log.debug('[%s] %s: no card ID entered, effect skipped', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (03-059):** No effect.')
        return

    log.debug('[%s] %s: specified card ID=%s', card_instance.card.effect, engine.player_label(player_index), specified_id)

    # Step 2: Player chooses a card from opponent's hand (blind selection)
    hand_size = len(opponent.hand)
    log.debug('[%s] %s: prompting to choose from opponent hand (size=%d)', card_instance.card.effect, engine.player_label(player_index), hand_size)
    selected_index = await engine._prompt_number_selection(
        player_index,
        min_value=1,
        max_value=hand_size,
        prompt_text=f"**Effect (03-059):** You specified: **{specified_id}**\nChoose a card from the opponent's hand:",
        placeholder='Select a card...',
        label_prefix='Card',
    )

    if selected_index is None:
        log.debug('[%s] %s: no card selected, effect skipped', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (03-059):** No effect.')
        return

    log.debug('[%s] %s: selected opponent hand index=%d', card_instance.card.effect, engine.player_label(player_index), selected_index)

    # Step 3: Reveal the selected card
    revealed = opponent.hand[selected_index - 1]
    revealed_name = revealed.card.name
    revealed_id = f'{revealed.card.pack:02d}-{revealed.card.id:03d}'
    log.debug('[%s] %s: revealed card=%s (ID=%s)', card_instance.card.effect, engine.player_label(player_index), revealed_name, revealed_id)

    import discord as _discord
    embed = _discord.Embed(title='Revealed Card [公開カード]', color=_discord.Color.gold())
    embed.description = card_detail_description(revealed)

    reveal_img = create_deck_grid_image([revealed], columns=1)
    await engine._send_dm(player_index, content='**Effect (03-059):** Revealed card:', embed=embed, file=reveal_img)

    reveal_img = create_deck_grid_image([revealed], columns=1)
    await engine._send_dm(1 - player_index, content=f'**Effect (03-059):** Your card was revealed: {revealed_name}.', file=reveal_img)

    reveal_img = create_deck_grid_image([revealed], columns=1)
    await engine._send_to_channel(content=f"**Effect (03-059):** A card was revealed from opponent's hand: {revealed_name}.", file=reveal_img)

    # Step 4: Check if card IDs match
    if specified_id.strip() == revealed_id:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 100
        log.debug('[%s] %s: match! specified=%s == revealed=%s, attack bonus +100 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), specified_id.strip(), revealed_id, old_bonus, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content='**Effect (03-059):** Match! Attack +100!')
    else:
        log.debug('[%s] %s: no match (specified=%s, revealed=%s), no bonus', card_instance.card.effect, engine.player_label(player_index), specified_id.strip(), revealed_id)
        await engine._send_dm(player_index, content='**Effect (03-059):** No match. No bonus.')
