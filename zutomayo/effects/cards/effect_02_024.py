from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_024(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    If the attribute of the character card used in the previous turn was electric
    and it is now night, choose a SEND TO POWER 1 card from your opponent's Power charger
    and place it at the bottom of your opponent's deck.
    """
    log.debug('[%s] %s: checking previous Electric + night condition', card_instance.card.effect, engine.player_label(player_index))
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is None or prev_char.attribute != Attribute.ELECTRICITY:
        log.debug('[%s] %s: previous character was not Electric, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content="**Effect (02-024):** Previous turn's character was not Electric. Effect fizzles.",
        )
        return

    if game_state.day_night != Chronos.NIGHT:
        log.debug('[%s] %s: it is not night (current=%s), effect fizzles', card_instance.card.effect, engine.player_label(player_index), game_state.day_night)
        await engine._send_dm(
            player_index,
            content="**Effect (02-024):** It is not night. Effect fizzles.",
        )
        return

    opponent = game_state.players[1 - player_index]

    # Filter opponent's power charger for send_to_power == 1
    targets = [card_instance for card_instance in opponent.power_charger if card_instance.card.send_to_power == 1]

    if not targets:
        log.debug('[%s] %s: no STP=1 cards in opponent power charger, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(
            player_index,
            content="**Effect (02-024):** No eligible cards (STP=1) in opponent's Power Charger. Effect fizzles.",
        )
        return

    log.debug('[%s] %s: found %d STP=1 targets, prompting selection', card_instance.card.effect, engine.player_label(player_index), len(targets))
    selected = await engine._prompt_card_selection(
        player_index,
        targets,
        prompt_text="**Effect (02-024):** Choose a card from opponent's Power Charger (STP=1) to place at the bottom of their deck.",
        placeholder='Select a card...',
    )

    if selected is None:
        log.debug('[%s] %s: no card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (02-024):** No effect.')
        return

    # Move selected card from opponent's power charger to bottom of opponent's deck
    if selected in opponent.power_charger:
        opponent.power_charger.remove(selected)
    selected.zone = Zone.DECK
    selected.face_up = False
    opponent.deck.append(selected)
    log.debug('[%s] %s: moved selected card %s from opponent power charger to bottom of opponent deck', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)
