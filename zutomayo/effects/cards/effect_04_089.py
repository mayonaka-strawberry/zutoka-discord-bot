from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.enums.song import Song
import logging

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


async def effect_04_089(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """
    If the card in the Battle Zone is a (TAIDADA) character, draw 1 card.
    If you drew a card due to this card's effect, your hand limit increases by 1 until the end of the game.
    """
    log.debug('[%s] %s: entering effect_04_089', card_instance.card.effect, engine.player_label(player_index))
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if player.battle_zone is None or player.battle_zone.card.song != Song.TAIDADA:
        await engine._send_dm(
            player_index,
            content='**Effect (04-089):** Battle Zone character is not TAIDADA. No effect.',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    if not player.can_draw(1):
        await engine._send_dm(
            player_index,
            content='**Effect (04-089):** Battle Zone is TAIDADA, but deck is empty. Cannot draw.',
        )
        log.debug('[%s] %s: early return, skipping effect', card_instance.card.effect, engine.player_label(player_index))
        return

    # Draw 1 card
    log.debug('[%s] %s: drawing %s card(s)', card_instance.card.effect, engine.player_label(player_index), 1)
    player.draw(1)
    await engine.notify_draw(game_state, player_index, 1)

    # Permanently increase hand size by 1
    player.pending_hand_size_bonus += 1
    log.debug('[%s] %s: hand size bonus +1 (pending bonus now %d)', card_instance.card.effect, engine.player_label(player_index), player.pending_hand_size_bonus)
    await engine._send_dm(
        player_index,
        content=f'**Effect (04-089):** Hand size permanently increased by 1! (Total bonus: +{player.hand_size_bonus + player.pending_hand_size_bonus})',
    )
    await engine._send_dm(
        opponent_index,
        content=f'**Effect (04-089):** Opponent\'s hand size permanently increased by 1. (Total bonus: +{player.hand_size_bonus + player.pending_hand_size_bonus})',
    )
