from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone
from zutomayo.ui.embeds import card_detail_description, create_deck_grid_image

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_03_097(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """Reveal the top card of the opponent's deck. If power cost >= 6, place it on the Power Charger."""
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if not opponent.deck:
        log.debug('[%s] %s: opponent deck is empty, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content="**Effect (03-097):** Opponent's deck is empty. No effect.")
        return

    top_card = opponent.deck[0]
    log.debug('[%s] %s: revealing top card of opponent %s deck: %s (power_cost=%d)', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index), top_card.card.name, top_card.card.power_cost)

    import discord as _discord
    embed = _discord.Embed(title='Revealed Card [公開カード]', color=_discord.Color.gold())
    embed.description = card_detail_description(top_card)

    # Send reveal image to all three recipients
    reveal_img = create_deck_grid_image([top_card], columns=1)
    await engine._send_dm(player_index, content="**Effect (03-097):** Revealed top card of opponent's deck:", embed=embed, file=reveal_img)

    embed2 = _discord.Embed(title='Revealed Card [公開カード]', color=_discord.Color.gold())
    embed2.description = card_detail_description(top_card)
    reveal_img = create_deck_grid_image([top_card], columns=1)
    await engine._send_dm(opponent_index, content=f'**Effect (03-097):** Top card of your deck was revealed: {top_card.card.name}.', embed=embed2, file=reveal_img)

    reveal_img = create_deck_grid_image([top_card], columns=1)
    await engine._send_to_channel(content=f"**Effect (03-097):** Top card of opponent's deck revealed: {top_card.card.name}.", file=reveal_img)

    player = game_state.players[player_index]

    if top_card.card.power_cost >= 6:
        log.debug('[%s] %s: power cost %d >= 6, moving to power charger', card_instance.card.effect, engine.player_label(player_index), top_card.card.power_cost)
        opponent.deck.pop(0)
        top_card.zone = Zone.POWER_CHARGER
        top_card.face_up = True
        top_card.attribute_override = None
        opponent.power_charger.append(top_card)
        log.debug('[%s] %s: moved %s to opponent power charger', card_instance.card.effect, engine.player_label(player_index), top_card.card.name)
        # 厳戒態勢 itself goes to Power Charger when this condition is met
        # (despite having no SEND TO POWER, per Q&A rules)
        if player.set_zone_c is card_instance:
            player.set_zone_c = None
            card_instance.zone = Zone.POWER_CHARGER
            card_instance.face_up = True
            card_instance.attribute_override = None
            player.power_charger.append(card_instance)
            log.debug('[%s] %s: self-destructed area enchant to power charger', card_instance.card.effect, engine.player_label(player_index))
        msg = f'**Effect (03-097):** Power cost {top_card.card.power_cost} >= 6. Card placed on Power Charger. 厳戒態勢 also sent to Power Charger.'
    else:
        log.debug('[%s] %s: power cost %d < 6, card stays on deck', card_instance.card.effect, engine.player_label(player_index), top_card.card.power_cost)
        msg = f'**Effect (03-097):** Power cost {top_card.card.power_cost} < 6. Card stays on deck.'

    await engine._send_dm(player_index, content=msg)
    await engine._send_dm(opponent_index, content=msg)
    log.debug('[%s] %s: effect complete', card_instance.card.effect, engine.player_label(player_index))
