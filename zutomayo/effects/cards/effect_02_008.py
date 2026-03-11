from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_008(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    If your character card's attribute is electric, choose one card from your opponent's
    Power Charger with SEND TO POWER 2 and place it at the bottom of your opponent's deck.
    """
    log.debug('[%s] %s: checking if own character is Electric', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent = game_state.players[1 - player_index]

    # Check own character is Electric
    if player.battle_zone is None or player.battle_zone.effective_attribute != Attribute.ELECTRICITY:
        log.debug('[%s] %s: character is not Electric, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content="**Effect (02-008):** Your character's attribute is not Electric. Effect fizzles.",
        )
        return

    log.debug('[%s] %s: character is Electric, checking opponent power charger for STP=2 cards', card_instance.card.effect, engine.player_label(player_index))
    # Filter opponent's power charger for send_to_power == 2
    targets = [card_instance for card_instance in opponent.power_charger if card_instance.card.send_to_power == 2]

    if not targets:
        log.debug('[%s] %s: no STP=2 cards in opponent power charger, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content="**Effect (02-008):** No eligible cards (STP=2) in opponent's Power Charger. Effect fizzles.",
        )
        return

    log.debug('[%s] %s: found %d STP=2 targets, prompting selection', card_instance.card.effect, engine.player_label(player_index), len(targets))
    selected = await engine._prompt_card_selection(
        player_index,
        targets,
        prompt_text="**Effect (02-008):** Choose a card from opponent's Power Charger (STP=2) to place at the bottom of their deck.",
        placeholder='Select a card...',
    )

    if selected is None:
        log.debug('[%s] %s: no card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (02-008):** No effect.')
        return

    # Move selected card from opponent's power charger to bottom of opponent's deck
    if selected in opponent.power_charger:
        opponent.power_charger.remove(selected)
    selected.zone = Zone.DECK
    selected.face_up = False
    opponent.deck.append(selected)
    log.debug('[%s] %s: moved selected card %s from opponent power charger to bottom of opponent deck', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)
