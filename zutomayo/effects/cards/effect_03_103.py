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


async def effect_03_103(engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
    """
    Reveal the top card of the opponent's deck.

    If the revealed card has no SEND TO POWER, Attack +30.
    If the revealed card has SEND TO POWER, immediately place this card on the Power Charger.
    """
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]
    player = game_state.players[player_index]

    if not opponent.deck:
        log.debug('[%s] %s: opponent deck is empty, no effect', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content="**Effect (03-103):** Opponent's deck is empty. No effect.")
        return

    top_card = opponent.deck[0]
    log.debug('[%s] %s: revealing top card of opponent %s deck: %s (send_to_power=%d)', card_instance.card.effect, engine.player_label(player_index), engine.player_label(opponent_index), top_card.card.name, top_card.card.send_to_power)

    import discord as _discord
    embed = _discord.Embed(title='Revealed Card [公開カード]', color=_discord.Color.gold())
    embed.description = card_detail_description(top_card)

    # Send reveal image to all three recipients
    reveal_img = create_deck_grid_image([top_card], columns=1)
    await engine._send_dm(player_index, content="**Effect (03-103):** Revealed top card of opponent's deck:", embed=embed, file=reveal_img)

    reveal_img = create_deck_grid_image([top_card], columns=1)
    await engine._send_dm(opponent_index, content=f'**Effect (03-103):** Top card of your deck was revealed: {top_card.card.name}.', file=reveal_img)

    reveal_img = create_deck_grid_image([top_card], columns=1)
    await engine._send_to_channel(content=f"**Effect (03-103):** Top card of opponent's deck revealed: {top_card.card.name}.", file=reveal_img)

    if top_card.card.send_to_power == 0:
        old_bonus = engine.turn_state.attack_bonus[player_index]
        engine.turn_state.attack_bonus[player_index] += 30
        log.debug('[%s] %s: no SEND TO POWER on revealed card, attack bonus +30 (%d -> %d)', card_instance.card.effect, engine.player_label(player_index), old_bonus, engine.turn_state.attack_bonus[player_index])
        await engine._send_dm(player_index, content='**Effect (03-103):** No SEND TO POWER on revealed card. Attack +30!')
        await engine._send_dm(opponent_index, content='**Effect (03-103):** Revealed card has no SEND TO POWER. Opponent gets Attack +30.')
    else:
        # Self-destruct: move this area enchant to the owner's Power Charger
        log.debug('[%s] %s: revealed card has SEND TO POWER, self-destructing area enchant to power charger', card_instance.card.effect, engine.player_label(player_index))
        player.set_zone_c = None
        card_instance.zone = Zone.POWER_CHARGER
        card_instance.face_up = True
        card_instance.attribute_override = None
        player.power_charger.append(card_instance)
        log.debug('[%s] %s: area enchant moved to power charger', card_instance.card.effect, engine.player_label(player_index))
        await engine._send_dm(player_index, content='**Effect (03-103):** Revealed card has SEND TO POWER. This card is placed on the Power Charger.')
        await engine._send_dm(opponent_index, content="**Effect (03-103):** Revealed card has SEND TO POWER. Opponent's area enchant sent to Power Charger.")
