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


async def effect_04_041(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    If you swapped with a (SHADE) character this turn, negate all effects of all
    enchantments controlled by your opponent. (Effects that have already resolved are unaffected.)
    """
    log.debug('[%s] %s: entering effect_04_041', card_instance.card.effect, engine.player_label(player_index))
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if Song.SHADE not in engine.turn_state.swapped_from_songs.get(player_index, set()):
        await engine._send_dm(player_index, content='**Effect (04-041):** Did not swap with a SHADE character this turn. No effect.')
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Disable all opponent's enchants that are played this turn (not yet resolved or upcoming)
    disabled_names: list[str] = []
    for zone_attribute in ('set_zone_a', 'set_zone_b'):
        enchant_card: CardInstance | None = getattr(opponent, zone_attribute)
        if (enchant_card is not None
                and enchant_card.card.card_type == CardType.ENCHANT
                and enchant_card.played_this_turn
                and not enchant_card.effects_disabled):
            enchant_card.effects_disabled = True
            log.debug('[%s] %s: disabled enchant %s', card_instance.card.effect, engine.player_label(player_index), enchant_card.card.name)
            disabled_names.append(enchant_card.card.name)

    if disabled_names:
        names_text = ', '.join(disabled_names)
        await engine._send_dm(player_index, content=f'**Effect (04-041):** Swapped with SHADE character. Disabled opponent enchant(s): {names_text}!')
        await engine._send_dm(opponent_index, content=f'**Effect (04-041):** Opponent swapped with SHADE character. Your enchant(s) disabled: {names_text}.')
    else:
        await engine._send_dm(player_index, content='**Effect (04-041):** Swapped with SHADE character, but opponent has no enchants to disable.')
        await engine._send_dm(opponent_index, content='**Effect (04-041):** Opponent swapped with SHADE character. No enchants to disable.')
