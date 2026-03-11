from __future__ import annotations
import random
from typing import TYPE_CHECKING
from zutomayo.enums.card_type import CardType
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_006(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Select 4 cards from the Abyss. Shuffle them face down and place them at the bottom of your deck.
    Otherwise, you lose the game.
    Then, select 1 character from the opponent's Power Charger, swap it with a character in the Battle Zone.
    The effects of the character brought into the Battle Zone by this effect are not triggered.
    """
    log.debug('[%s] %s: entering effect_04_006', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Phase 1: Check if player has at least 4 cards in Abyss
    if len(player.abyss) < 4:
        player.hp = 0
        log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content=f'**Effect (04-006):** Only {len(player.abyss)} card(s) in Abyss (need 4). You lose the game!',
        )
        await engine._send_dm(
            opponent_index,
            content=f'**Effect (04-006):** Opponent only has {len(player.abyss)} card(s) in Abyss (need 4). Opponent loses!',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Phase 2: Select 4 cards from Abyss, shuffle, place at bottom of deck
    remaining_abyss = list(player.abyss)
    selected_cards: list[CardInstance] = []

    for selection_number in range(1, 5):
        selected_card = await engine._prompt_card_selection(
            player_index,
            remaining_abyss,
            prompt_text=f'**Effect (04-006):** Choose card #{selection_number} of 4 from the Abyss to return to deck bottom.',
            placeholder='Select a card from Abyss...',
        )
        log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

        if selected_card is None:
            # Timeout or no selection — player loses the game (mandatory action)
            player.hp = 0
            log.debug('[%s] %s: HP set to 0, player loses', card_instance.card.effect, engine.player_label(player_index))
            await engine._send_dm(
                player_index,
                content='**Effect (04-006):** Failed to select 4 cards. You lose the game!',
            )
            await engine._send_dm(
                opponent_index,
                content='**Effect (04-006):** Opponent failed to select 4 cards from Abyss. Opponent loses!',
            )
            log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
            return

        selected_cards.append(selected_card)
        remaining_abyss.remove(selected_card)

    # Remove selected cards from abyss
    for selected_card in selected_cards:
        player.abyss.remove(selected_card)

    # Shuffle the 4 cards face down and place at the bottom of the deck
    random.shuffle(selected_cards)
    log.debug('[%s] %s: shuffled selected cards', card_instance.card.effect, engine.player_label(player_index))
    for selected_card in selected_cards:
        selected_card.zone = Zone.DECK
        selected_card.face_up = False
        player.deck.append(selected_card)

    returned_names = ', '.join(selected_card.card.name for selected_card in selected_cards)
    await engine._send_dm(
        player_index,
        content=f'**Effect (04-006):** Returned 4 cards to deck bottom (shuffled): {returned_names}.',
    )
    await engine._send_dm(
        opponent_index,
        content='**Effect (04-006):** Opponent returned 4 cards from Abyss to the bottom of their deck (shuffled).',
    )

    # Phase 3: Swap opponent's Power Charger character with their Battle Zone character
    opponent_power_characters = [
        power_card for power_card in opponent.power_charger
        if power_card.card.card_type == CardType.CHARACTER
    ]

    if not opponent_power_characters:
        await engine._send_dm(
            player_index,
            content="**Effect (04-006):** No characters in opponent's Power Charger. Swap fizzles.",
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    if opponent.battle_zone is None:
        await engine._send_dm(
            player_index,
            content="**Effect (04-006):** Opponent has no character in Battle Zone. Swap fizzles.",
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selected_power_character = await engine._prompt_card_selection(
        player_index,
        opponent_power_characters,
        prompt_text="**Effect (04-006):** Choose a character from opponent's Power Charger to swap into their Battle Zone.",
        placeholder="Select opponent's character...",
    )
    log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_power_character.card.name if selected_power_character else None)

    if selected_power_character is None:
        await engine._send_dm(player_index, content='**Effect (04-006):** No swap performed.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Perform the swap
    old_battle_character = opponent.battle_zone

    # Move old battle zone character to power charger or abyss
    old_battle_character.attribute_override = None
    if old_battle_character.card.send_to_power > 0:
        old_battle_character.zone = Zone.POWER_CHARGER
        old_battle_character.face_up = True
        opponent.power_charger.append(old_battle_character)
    else:
        old_battle_character.zone = Zone.ABYSS
        old_battle_character.face_up = True
        opponent.abyss.append(old_battle_character)

    # Move selected power charger character to battle zone (effects disabled)
    opponent.power_charger.remove(selected_power_character)
    selected_power_character.zone = Zone.BATTLE_ZONE
    selected_power_character.face_up = True
    selected_power_character.effects_disabled = True
    log.debug('[%s] %s: disabled effects on swapped character', card_instance.card.effect, engine.player_label(player_index))
    opponent.battle_zone = selected_power_character
    log.debug('[%s] %s: set opponent battle zone to %s', card_instance.card.effect, engine.player_label(player_index), selected_power_character.card.name)

    await engine._send_dm(
        player_index,
        content=f'**Effect (04-006):** Swapped {old_battle_character.card.name} out of opponent\'s Battle Zone. '
                f'{selected_power_character.card.name} moved in (effects disabled).',
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-006):** Opponent swapped your {old_battle_character.card.name} out of Battle Zone. '
                f'{selected_power_character.card.name} moved in from Power Charger (effects disabled).',
    )
