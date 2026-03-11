from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.card_type import CardType
from zutomayo.enums.song import Song
from zutomayo.enums.zone import Zone
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_053(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    You may place a (STUDY ME) character card from your hand and place it on the Power Charger.
    If you do, draw 1 card.
    """
    log.debug('[%s] %s: entering effect_04_053', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    study_me_characters = [
        hand_card for hand_card in player.hand
        if hand_card.card.song == Song.STUDY_ME
        and hand_card.card.card_type == CardType.CHARACTER
    ]

    if not study_me_characters:
        await engine._send_dm(player_index, content='**Effect (04-053):** No STUDY ME characters in hand. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        study_me_characters,
        prompt_text='**Effect (04-053):** Choose a STUDY ME character from your hand to place on the Power Charger (or let it time out to skip).',
        placeholder='Select a STUDY ME character...',
    )
    log.debug('[%s] %s: card selection result: %s', card_instance.card.effect, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        await engine._send_dm(player_index, content='**Effect (04-053):** No card selected. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Move selected card from hand to power charger
    player.hand.remove(selected_card)
    selected_card.zone = Zone.POWER_CHARGER
    selected_card.face_up = True
    player.power_charger.append(selected_card)

    await engine._send_dm(player_index, content=f'**Effect (04-053):** Placed {selected_card.card.name} on the Power Charger.')
    await engine._send_dm(opponent_index, content=f'**Effect (04-053):** Opponent placed {selected_card.card.name} on their Power Charger.')

    # Draw 1 card
    if player.can_draw(1):
        log.debug('[%s] %s: drawing %s card(s)', card_instance.card.effect, engine.player_label(player_index), 1)
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)
