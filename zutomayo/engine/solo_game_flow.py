"""
Single-player game flow for playing against メカうにぐり.

Subclasses GameFlow. Since the decision-broker refactor the turn logic lives
entirely in GameFlow: the bot's decisions arrive through the
BotAgentDecisionAdapter installed for player index 1, and the transport skips
DMs (and their renders) to the bot's sentinel Discord id. What remains here is
what is genuinely solo-specific: agent construction and the deck-building
phase where the bot picks its own deck.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
import discord
from zutomayo.engine.bot_agent import (
    BotAgent, ModelBotAgentV2,
    create_bot_agent,
    load_random_best_deck_v2, load_random_best_deck_v2_easy,
)
from zutomayo.engine.game_flow import GameFlow
from zutomayo.ui.deck_management_views import DeckSourceView

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card


log = logging.getLogger(__name__)

HUMAN_PLAYER_INDEX = 0
BOT_PLAYER_INDEX = 1


class SoloGameFlow(GameFlow):
    """
    Game flow for single-player mode against the UNIGURI bot.

    The human is always player index 0, the bot is always player index 1.
    """

    def __init__(
        self,
        bot: discord.Client,
        bot_agent: BotAgent | None = None,
        use_easy_decks: bool = False,
    ) -> None:
        super().__init__(bot)
        self.bot_agent = bot_agent if bot_agent is not None else create_bot_agent()
        self.use_easy_decks = use_easy_decks

    def _ensure_decision_runtime(self, session: GameSession) -> None:
        """Human decisions go through Discord views; bot decisions through the agent."""
        from zutomayo.engine.adapters.bot_agent_adapter import BotAgentDecisionAdapter
        from zutomayo.engine.adapters.discord_adapter import DiscordDecisionAdapter
        from zutomayo.engine.decision_broker import DecisionBroker
        from zutomayo.engine.match_transport import DiscordMatchTransport

        if session.transport is None:
            session.transport = DiscordMatchTransport(self.bot)
        if session.broker is None:
            session.broker = DecisionBroker(session, {
                HUMAN_PLAYER_INDEX: DiscordDecisionAdapter(session.transport),
                BOT_PLAYER_INDEX: BotAgentDecisionAdapter(self.bot_agent),
            })

    async def run_solo_game(self, session: GameSession) -> None:
        """Entry point for a single-player game against UNIGURI."""
        try:
            self._ensure_decision_runtime(session)
            deck_1_cards, deck_2_cards = await self._do_deck_building_phase(session)
            await self.run_single_match(session, deck_1_cards, deck_2_cards)

            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)

        except Exception:
            log.exception('Error in solo game flow')
            await self._send_to_channel(session, content='An error occurred. Game ended.')
            await self._send_to_player(session, HUMAN_PLAYER_INDEX, content='An error occurred. Game ended.')
            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)

    async def _do_deck_building_phase(
        self, session: GameSession,
    ) -> tuple[list[Card] | None, list[Card] | None]:
        """Human selects deck normally; bot picks a random saved deck."""
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_validator import build_card_index

        all_cards = load_cards()
        card_index = build_card_index(all_cards)
        session.clear_pending()
        names = self._player_names(session)

        # Send deck source view to human player only
        view = DeckSourceView(
            session=session,
            player_index=HUMAN_PLAYER_INDEX,
            all_cards=all_cards,
            card_index=card_index,
            opponent_name=names[BOT_PLAYER_INDEX],
        )
        await self._send_to_player(
            session, HUMAN_PLAYER_INDEX,
            content=(
                '**Deck Building [デッキ構築]**\n'
                'Choose how to build your deck:\n'
                '**Build a Deck** - Enter cards manually\n'
                '**Select a Deck** - Use one of your saved decks\n'
                '**Select a Default Deck** - Use a pre-built deck'
            ),
            view=view,
        )

        # Bot selects a deck from the best evaluated decks (or falls back to any saved deck)
        try:
            if isinstance(self.bot_agent, ModelBotAgentV2):
                if self.use_easy_decks:
                    bot_deck_cards = load_random_best_deck_v2_easy(card_index)
                else:
                    bot_deck_cards = load_random_best_deck_v2(card_index)
            else:
                bot_deck_cards = load_random_best_deck_v2(card_index)
        except ValueError:
            log.warning('No saved decks found, using random deck for bot')
            bot_deck_cards = None

        session.player_deck_names[BOT_PLAYER_INDEX] = '<bot>'
        session.submit_action(BOT_PLAYER_INDEX, bot_deck_cards)

        # Wait for human player only
        await session.wait_for_both_players(timeout=750.0)

        human_deck = session.pending_actions.get(HUMAN_PLAYER_INDEX)
        bot_deck = session.pending_actions.get(BOT_PLAYER_INDEX)
        return human_deck, bot_deck
