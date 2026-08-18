"""
Solo games: a human versus a trained model opponent, on the same match
runtime as two-player games. The model answers through a decision adapter,
so solo games persist, replay, and resume exactly like any other match (past
model decisions replay from the log; the model is only consulted live).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from zutomayo.match.broker import MatchDecisionBroker
from zutomayo.match.discord_adapter import DiscordMatchDecisionAdapter
from zutomayo.match.match_flow import SingleMatchFlow
from zutomayo.match.transport import DiscordMatchTransport

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession

log = logging.getLogger(__name__)

HUMAN_PLAYER_INDEX = 0
MODEL_PLAYER_INDEX = 1


def create_model_adapter(session: 'GameSession', opponent: str):
    """Build the decision adapter for a solo opponent. Raises ValueError when
    the opponent has no deployable checkpoint."""
    from zutomayo.match.agents import available_solo_opponents, solo_opponent_label
    from zutomayo.match.agents.agent_adapter import ModelDecisionAdapter, create_solo_agent

    if opponent not in available_solo_opponents():
        raise ValueError(f'Model {solo_opponent_label(opponent)} is not available.')
    return ModelDecisionAdapter(create_solo_agent(opponent), lambda: session.broker)


async def _do_solo_deck_building(flow: SingleMatchFlow, session: 'GameSession'):
    """Deck building for the human seat only; the model gets a random deck.
    Returns the chosen Card list, or None on timeout (random deck)."""
    from zutomayo.data.deck_validator import get_card_index
    from zutomayo.ui.deck_management_views import DeckSourceView
    from zutomayo.match.agents import BOT_NAME

    all_cards, card_index = get_card_index()
    session.clear_pending()
    view = DeckSourceView(
        session=session,
        player_index=HUMAN_PLAYER_INDEX,
        all_cards=all_cards,
        card_index=card_index,
        opponent_name=BOT_NAME,
    )
    await session.transport.send_to_player(
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
    await session.wait_for_player(HUMAN_PLAYER_INDEX, timeout=750.0)
    return session.pending_actions.get(HUMAN_PLAYER_INDEX)


async def run_solo_game(bot: discord.Client, session: 'GameSession', opponent: str) -> None:
    from zutomayo.data.deck_validator import get_card_index
    from zutomayo.engine.game_session import session_manager
    from zutomayo.match.agents import load_random_fallback_deck

    flow = SingleMatchFlow(bot)
    try:
        if session.transport is None:
            session.transport = DiscordMatchTransport(bot)
        if session.broker is None:
            session.broker = MatchDecisionBroker(session, {
                HUMAN_PLAYER_INDEX: DiscordMatchDecisionAdapter(session.transport),
                MODEL_PLAYER_INDEX: create_model_adapter(session, opponent),
            })

        deck_cards = await _do_solo_deck_building(flow, session)
        _, card_index = get_card_index()
        model_deck_cards = load_random_fallback_deck(card_index)

        await flow.run_single_match(session, deck_cards, model_deck_cards)
        await flow.finalize_completed_game(session)
        session_manager.remove_game(session.game_id)
    except Exception:
        log.exception('Error in solo game flow')
        if session.transport is not None:
            await session.transport.send_to_player(
                session, HUMAN_PLAYER_INDEX, content='An error occurred. Game ended.',
            )
        await flow.mark_game_abandoned(session)
        session_manager.remove_game(session.game_id)
