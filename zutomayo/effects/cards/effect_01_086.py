from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_086(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Swap one card in hand with one card of your choice from the Abyss."""
    player = game_state.players[player_index]

    log.debug('[%s] %s: checking hand size=%d, abyss size=%d', card_instance.card.effect, engine.player_label(player_index), len(player.hand), len(player.abyss))

    if not player.hand or not player.abyss:
        log.debug('[%s] %s: hand or abyss empty, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (01-086):** You need cards in both your hand and Abyss. Effect fizzles.',
        )
        return

    # Step 1: Select a card from hand to send to Abyss
    hand_selection = await engine._prompt_card_selection(
        player_index,
        player.hand,
        prompt_text='**Effect (01-086):** Choose a card from your hand to send to the Abyss.',
        placeholder='Select a hand card...',
    )

    if hand_selection is None:
        log.debug('[%s] %s: no hand card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (01-086):** No effect.',
        )
        return

    log.debug('[%s] %s: selected hand card %s to send to abyss', card_instance.card.effect, engine.player_label(player_index), hand_selection.card.effect)

    # Step 2: Select a card from Abyss to add to hand
    abyss_selection = await engine._prompt_card_selection(
        player_index,
        player.abyss,
        prompt_text='**Effect (01-086):** Choose a card from the Abyss to add to your hand.',
        placeholder='Select an Abyss card...',
    )

    if abyss_selection is None:
        log.debug('[%s] %s: no abyss card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content='**Effect (01-086):** No effect.',
        )
        return

    log.debug('[%s] %s: selected abyss card %s to add to hand', card_instance.card.effect, engine.player_label(player_index), abyss_selection.card.effect)

    # Perform the swap
    player.hand.remove(hand_selection)
    hand_selection.zone = Zone.ABYSS
    hand_selection.face_up = True
    player.abyss.append(hand_selection)

    player.abyss.remove(abyss_selection)
    abyss_selection.zone = Zone.HAND
    abyss_selection.face_up = False
    player.hand.append(abyss_selection)

    log.debug('[%s] %s: swapped hand card %s <-> abyss card %s', card_instance.card.effect, engine.player_label(player_index), hand_selection.card.effect, abyss_selection.card.effect)
