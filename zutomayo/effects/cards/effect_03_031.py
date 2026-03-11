from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_031(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Choose a card from your hand, regardless of its Power, and place it in the Abyss. Draw a card from the deck."""
    player = game_state.players[player_index]

    if not player.hand:
        log.debug('[%s] %s: no cards in hand, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (03-031):** No cards in hand. Effect fizzles.')
        return

    log.debug('[%s] %s: prompting to select a card from hand (hand size=%d)', card_instance.card.effect, engine.player_label(player_index), len(player.hand))
    selected = await engine._prompt_card_selection(
        player_index,
        player.hand,
        prompt_text='**Effect (03-031):** Choose a card from your hand to place in the Abyss.',
        placeholder='Select a hand card...',
    )

    if selected is None:
        log.debug('[%s] %s: no card selected, effect skipped', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (03-031):** No effect.')
        return

    log.debug('[%s] %s: selected card %s to send to abyss', card_instance.card.effect, engine.player_label(player_index), selected.card.name)

    # Move selected card from hand to Abyss
    player.hand.remove(selected)
    selected.zone = Zone.ABYSS
    selected.face_up = True
    player.abyss.append(selected)
    log.debug('[%s] %s: moved %s from hand to abyss (hand size=%d, abyss size=%d)', card_instance.card.effect, engine.player_label(player_index), selected.card.name, len(player.hand), len(player.abyss))

    # Draw a card from the deck
    if player.can_draw(1):
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)
        log.debug('[%s] %s: drew 1 card from deck', card_instance.card.effect, engine.player_label(player_index))
    else:
        log.debug('[%s] %s: cannot draw, deck empty', card_instance.card.effect, engine.player_label(player_index))
