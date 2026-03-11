from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.card_type import CardType
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_002(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Select up to 2 characters from the Power Charger (SHADE) and use their effects."""
    log.debug('[%s] %s: entering effect_04_002', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]

    shade_characters = [
        power_card for power_card in player.power_charger
        if power_card.card.song == Song.SHADE
        and power_card.card.card_type == CardType.CHARACTER
        and power_card.card.effect
    ]

    if not shade_characters:
        await engine._send_dm(
            player_index,
            content='**Effect (04-002):** No SHADE characters with effects in Power Charger. No effect.',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    max_selections = min(2, len(shade_characters))
    selection_count = await engine._prompt_number_selection(
        player_index,
        min_value=0,
        max_value=max_selections,
        prompt_text=f'**Effect (04-002):** You have {len(shade_characters)} SHADE character(s) with effects in Power Charger. How many do you want to use (up to 2)?',
        placeholder='Select number of effects to use...',
    )
    log.debug('[%s] %s: number selection result: %s', card_instance.card.effect, engine.player_label(player_index), selection_count)

    if not selection_count:
        await engine._send_dm(player_index, content='**Effect (04-002):** No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    remaining_candidates = list(shade_characters)
    selected_cards: list[CardInstance] = []

    for selection_number in range(1, selection_count + 1):
        selected_card = await engine._prompt_card_selection(
            player_index,
            remaining_candidates,
            prompt_text=f'**Effect (04-002):** Choose SHADE character #{selection_number} to use its effect.',
            placeholder='Select a SHADE character...',
        )
        log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

        if selected_card is None:
            break

        selected_cards.append(selected_card)
        remaining_candidates.remove(selected_card)

    if not selected_cards:
        await engine._send_dm(player_index, content='**Effect (04-002):** No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    for selected_card in selected_cards:
        card_name = selected_card.card.name
        await engine._send_dm(player_index, content=f'**Effect (04-002):** Activating effect of {card_name} ({selected_card.card.effect}).')
        await engine._send_dm(1 - player_index, content=f'**Effect (04-002):** Opponent activates effect of {card_name} ({selected_card.card.effect}) from Power Charger.')
        log.debug('[%s] %s: dispatching sub-effect %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.effect)
        await engine._dispatch(game_state, player_index, selected_card)
