"""
Bot-aware effect engine for single-player mode.

Subclasses EffectEngine to auto-respond for the bot player (player index 1)
when interactive prompts are needed. Human player (player index 0) still gets
normal Discord UI prompts.
"""

from __future__ import annotations
import logging
import random
from typing import TYPE_CHECKING, Any, Callable, Optional
from zutomayo.effects.effect_engine import EffectEngine
from zutomayo.engine.bot_agent import BOT_NAME, BotAgent

if TYPE_CHECKING:
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)

HUMAN_PLAYER_INDEX = 0
BOT_PLAYER_INDEX = 1


class BotEffectEngine(EffectEngine):
    """
    EffectEngine that auto-resolves interactive prompts for the bot player.

    For human players, delegates to the parent EffectEngine which sends
    Discord DM views. For the bot player, returns random valid choices
    immediately without any Discord interaction.
    """

    def __init__(self, bot_agent: BotAgent) -> None:
        super().__init__()
        self.bot_agent = bot_agent

    async def _prompt_effect_order(
        self,
        player_index: int,
        eligible: list[CardInstance],
    ) -> list[CardInstance]:
        if player_index == BOT_PLAYER_INDEX:
            log.debug('UNIGURI auto-ordering %d effects', len(eligible))
            return self.bot_agent.choose_effect_order(eligible)
        return await super()._prompt_effect_order(player_index, eligible)

    async def _prompt_card_selection(
        self,
        player_index: int,
        cards: list[CardInstance],
        prompt_text: str,
        placeholder: str = 'Select a card...',
    ) -> Optional[CardInstance]:
        if player_index == BOT_PLAYER_INDEX:
            log.debug('UNIGURI auto-selecting card from %d options', len(cards))
            return self.bot_agent.choose_effect_card(cards)
        return await super()._prompt_card_selection(
            player_index, cards, prompt_text, placeholder,
        )

    async def _prompt_number_selection(
        self,
        player_index: int,
        min_value: int,
        max_value: int,
        prompt_text: str,
        placeholder: str = 'Select a number...',
        label_prefix: str | None = None,
    ) -> int | None:
        if player_index == BOT_PLAYER_INDEX:
            chosen = self.bot_agent.choose_effect_number(min_value, max_value)
            log.debug('UNIGURI auto-selected number: %d', chosen)
            return chosen
        return await super()._prompt_number_selection(
            player_index, min_value, max_value, prompt_text,
            placeholder, label_prefix,
        )

    async def _prompt_text_input(
        self,
        player_index: int,
        prompt_text: str,
        modal_title: str = 'Specify a card',
        button_label: str = 'Enter Card ID',
        input_label: str | None = None,
        input_placeholder: str | None = None,
        validator: Callable[[str], str | None] | None = None,
    ) -> str | None:
        if player_index == BOT_PLAYER_INDEX:
            log.debug('UNIGURI auto-skipping text input prompt')
            return self.bot_agent.choose_effect_text()
        return await super()._prompt_text_input(
            player_index, prompt_text, modal_title, button_label,
            input_label, input_placeholder, validator,
        )

    async def notify_draw(self, game_state: GameState, player_index: int, count: int) -> None:
        if player_index == BOT_PLAYER_INDEX and count > 0:
            card_word = 'card' if count == 1 else 'cards'
            msg = f'**{BOT_NAME}** drew **{count}** {card_word}.'
            await self._send_to_channel(content=msg)
            await self._send_dm(HUMAN_PLAYER_INDEX, content=msg)
            return
        await super().notify_draw(game_state, player_index, count)

    async def _send_dm(self, player_index: int, **kwargs: Any) -> None:
        """Skip sending DMs to the bot player."""
        if player_index == BOT_PLAYER_INDEX:
            return None
        return await super()._send_dm(player_index, **kwargs)
