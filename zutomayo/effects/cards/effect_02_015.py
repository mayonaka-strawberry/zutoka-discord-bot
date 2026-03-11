from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_015(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    If the attribute of the character card used in the previous turn is darkness
    and it is now daytime, you may use an additional enchantment from your hand.
    Then draw a card.
    """
    log.debug('[%s] %s: checking previous character darkness + daytime condition', card_instance.card.effect, engine.player_label(player_index))
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is None or prev_char.attribute != Attribute.DARKNESS:
        log.debug('[%s] %s: previous character was not Darkness (prev_char=%s), skipping', card_instance.card.effect, engine.player_label(player_index), prev_char.attribute if prev_char else None)
        return
    if game_state.day_night != Chronos.DAY:
        log.debug('[%s] %s: it is not daytime (current=%s), skipping', card_instance.card.effect, engine.player_label(player_index), game_state.day_night)
        return

    log.debug('[%s] %s: conditions met (prev Darkness + daytime), offering extra enchantment', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]

    # Filter hand for enchant cards with effects
    hand_enchants = [
        ci for ci in player.hand
        if ci.card.card_type == CardType.ENCHANT and ci.card.effect
    ]

    if hand_enchants:
        log.debug('[%s] %s: %d enchantments available in hand, prompting selection', card_instance.card.effect, engine.player_label(player_index), len(hand_enchants))
        selected = await engine._prompt_card_selection(
            player_index,
            hand_enchants,
            prompt_text='**Effect (02-015):** Choose an enchantment from your hand to use (or wait for timeout to skip).',
            placeholder='Select an enchantment...',
        )

        if selected is not None:
            log.debug('[%s] %s: selected enchantment %s, dispatching its effect', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)
            # Execute the enchant's effect
            await engine._dispatch(game_state, player_index, selected)
            # Move the enchant from hand to abyss (it was used outside a set zone)
            if selected in player.hand:
                player.hand.remove(selected)
            selected.zone = Zone.ABYSS
            selected.face_up = True
            player.abyss.append(selected)
            log.debug('[%s] %s: moved enchantment %s from hand to abyss', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)
        else:
            log.debug('[%s] %s: no enchantment selected', card_instance.card.effect, engine.player_label(player_index))
    else:
        log.debug('[%s] %s: no enchantments available in hand', card_instance.card.effect, engine.player_label(player_index))

    # Draw a card
    if player.deck:
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)
        log.debug('[%s] %s: drew 1 card', card_instance.card.effect, engine.player_label(player_index))
    else:
        log.debug('[%s] %s: deck empty, cannot draw', card_instance.card.effect, engine.player_label(player_index))
