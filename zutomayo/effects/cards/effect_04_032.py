from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.zone import Zone
from zutomayo.ui.embeds import create_deck_grid_image
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_032(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    Reveal your hand. Attack +50 if it contains 4 or more attributes.
    If there is an Area Enchantment on your opponent's side, immediately place it into the Abyss.
    """
    log.debug('[%s] %s: entering effect_04_032', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    # Part 1: Reveal hand and check attributes
    if not player.hand:
        await engine._send_dm(player_index, content='**Effect (04-032):** Hand is empty. No attribute bonus.')
    else:
        hand_attributes = {hand_card.card.attribute for hand_card in player.hand}
        hand_card_names = ', '.join(hand_card.card.name for hand_card in player.hand)

        reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
        await engine._send_dm(player_index, content='**Effect (04-032):** Your revealed hand:', file=reveal_img)

        reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
        await engine._send_dm(opponent_index, content=f'**Effect (04-032):** Opponent reveals hand: {hand_card_names}.', file=reveal_img)

        reveal_img = create_deck_grid_image(player.hand, columns=len(player.hand))
        await engine._send_to_channel(content=f'**Effect (04-032):** Opponent reveals hand: {hand_card_names}.', file=reveal_img)

        if len(hand_attributes) >= 4:
            engine.turn_state.attack_bonus[player_index] += 50
            log.debug('[%s] %s: attack bonus +%d (now %d)', card_instance.card.effect, engine.player_label(player_index), 50, engine.turn_state.attack_bonus[player_index])
            attribute_names = ', '.join(attribute.value for attribute in hand_attributes)
            await engine._send_dm(player_index, content=f'**Effect (04-032):** Hand revealed with {len(hand_attributes)} attributes ({attribute_names}). Attack +50!')
            await engine._send_dm(opponent_index, content=f'**Effect (04-032):** Opponent has {len(hand_attributes)} attributes in hand. Attack +50.')
        else:
            attribute_names = ', '.join(attribute.value for attribute in hand_attributes)
            await engine._send_dm(player_index, content=f'**Effect (04-032):** Hand revealed with only {len(hand_attributes)} attribute(s) ({attribute_names}). Need 4+. No bonus.')

    # Part 2: Destroy opponent's area enchant (independent of attribute check)
    opponent_area_enchant = opponent.set_zone_c
    if opponent_area_enchant is not None:
        opponent.set_zone_c = None
        log.debug('[%s] %s: removed opponent area enchant', card_instance.card.effect, engine.player_label(player_index))
        opponent_area_enchant.zone = Zone.ABYSS
        opponent_area_enchant.face_up = True
        opponent_area_enchant.attribute_override = None
        opponent.abyss.append(opponent_area_enchant)
        await engine._send_dm(
            player_index,
            content=f"**Effect (04-032):** Opponent's Area Enchant ({opponent_area_enchant.card.name}) placed into Abyss!",
        )
        await engine._send_dm(
            opponent_index,
            content=f'**Effect (04-032):** Your Area Enchant ({opponent_area_enchant.card.name}) was placed into your Abyss.',
        )
    else:
        await engine._send_dm(player_index, content='**Effect (04-032):** Opponent has no Area Enchant. No removal.')
