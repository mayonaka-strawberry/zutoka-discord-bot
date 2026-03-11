from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from constants import CHRONOS_SIZE
from zutomayo.enums.attribute import Attribute

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_02_011(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    If the attribute of the character card used in the previous turn was flame,
    you can move the clock forward 0 to 5.
    """
    log.debug('[%s] %s: checking if previous character was Flame', card_instance.card.effect, engine.player_label(player_index))
    prev_char = game_state.previous_battle_characters.get(player_index)
    if prev_char is None or prev_char.attribute != Attribute.FLAME:
        log.debug('[%s] %s: previous character was not Flame (prev_char=%s), effect fizzles', card_instance.card.effect, engine.player_label(player_index), prev_char.attribute if prev_char else None)
        await engine._send_dm(
            player_index,
            content="**Effect (02-011):** Previous turn's character was not Flame. Effect fizzles.",
        )
        return

    log.debug('[%s] %s: previous character was Flame, prompting clock advance 0-5', card_instance.card.effect, engine.player_label(player_index))
    selected = await engine._prompt_number_selection(
        player_index,
        min_value=0,
        max_value=5,
        prompt_text='**Effect (02-011):** Choose how many steps to advance the clock (0-5).',
        placeholder='Select clock advance...',
    )

    if selected is None:
        log.debug('[%s] %s: no selection made, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (02-011):** No effect.')
        return

    log.debug('[%s] %s: selected clock advance of %d', card_instance.card.effect, engine.player_label(player_index), selected)
    if selected > 0:
        old_chronos = game_state.chronos
        engine.set_chronos(game_state, (game_state.chronos + selected) % CHRONOS_SIZE)
        log.debug('[%s] %s: advanced clock by %d (from %d to %d)', card_instance.card.effect, engine.player_label(player_index), selected, old_chronos, game_state.chronos)
