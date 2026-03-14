from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.card_type import CardType

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_01_006(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Choose any one of the Abyss enchantment cards and use it in this turn."""
    player = game_state.players[player_index]

    # Filter abyss for enchant cards with effects
    abyss_enchants = [
        ci for ci in player.abyss
        if ci.card.card_type == CardType.ENCHANT and ci.card.effect
        and ci.card.effect != card_instance.card.effect
    ]

    log.debug('[%s] %s: searching abyss for enchant cards (abyss size=%d)', card_instance.card.effect, engine.player_label(player_index), len(player.abyss))

    if not abyss_enchants:
        log.debug('[%s] %s: no enchantment cards in abyss, effect fizzles', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (01-006):** No enchantment cards in your Abyss. Effect fizzles.')
        return

    log.debug('[%s] %s: found %d abyss enchant candidates', card_instance.card.effect, engine.player_label(player_index), len(abyss_enchants))

    selected = await engine._prompt_card_selection(
        player_index,
        abyss_enchants,
        prompt_text='**Effect (01-006):** Choose an enchantment card from your Abyss to use this turn.',
        placeholder='Select an Abyss enchantment...',
    )

    if selected is None:
        log.debug('[%s] %s: no card selected, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (01-006):** No effect.')
        return

    log.debug('[%s] %s: selected abyss enchant %s, dispatching its effect', card_instance.card.effect, engine.player_label(player_index), selected.card.effect)
    # Execute the chosen enchant's effect via the engine's dispatcher
    # (the card stays in abyss; this borrows 01-006's activation)
    await engine._dispatch(game_state, player_index, selected)
