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


async def effect_04_094(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Select a (SHADE) character from the Power Charger and use it as this card's effect.

    If there are 5 or more cards placed on the Power Charger, immediately place this card
    into the Abyss (handled by check_area_enchant_removal).
    """
    log.debug('[%s] %s: entering effect_04_094', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    shade_characters = [
        power_card for power_card in player.power_charger
        if power_card.card.song == Song.SHADE
        and power_card.card.card_type == CardType.CHARACTER
        and power_card.card.effect
    ]

    if not shade_characters:
        await engine._send_dm(
            player_index,
            content='**Effect (04-094):** No SHADE characters with effects in Power Charger. No effect.',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        shade_characters,
        prompt_text='**Effect (04-094):** Choose a SHADE character from Power Charger to use its effect.',
        placeholder='Select a SHADE character...',
    )
    log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        await engine._send_dm(player_index, content='**Effect (04-094):** No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    card_name = selected_card.card.name
    await engine._send_dm(player_index, content=f'**Effect (04-094):** Activating effect of {card_name} ({selected_card.card.effect}).')
    await engine._send_dm(opponent_index, content=f'**Effect (04-094):** Opponent activates effect of {card_name} ({selected_card.card.effect}) from Power Charger.')
    log.debug('[%s] %s: dispatching sub-effect %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.effect)
    await engine._dispatch(game_state, player_index, selected_card)
