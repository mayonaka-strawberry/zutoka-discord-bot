"""
SingleMatchFlow: one standard match run on the engine_alpha state machine.

Owns everything around the engine: deck building, the opening hand DMs, the
driver loop, result recording (Elo, game status), and forfeit handling. Phase
gates - the board, zone strips and field embed posted at every phase boundary -
belong to the narrator's GatePresenter, which this flow hands its renderers to.
The engine owns every rule; this flow never mutates game state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

import discord

from zutomayo.match.broker import MatchDecisionBroker, MatchResumeDivergenceError
from zutomayo.match.discord_adapter import DiscordMatchDecisionAdapter
from zutomayo.match.match_driver import EngineMatchDriver, MatchOutcome
from zutomayo.match.narrator import MatchNarrator
from zutomayo.match.persistence import MatchRecordStore, card_keys_for_definition_indices
from zutomayo.match.state_view import project_board_view
from zutomayo.match.transport import DiscordMatchTransport

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card

log = logging.getLogger(__name__)


def definition_indices_for_cards(cards: list['Card']) -> list[int]:
    from engine_alpha.cards import KEY_TO_INDEX

    return [KEY_TO_INDEX[f'{card.pack:02d}-{card.id:03d}'] for card in cards]


def should_suppress_winner_elo_gain(
    state: Any, mode: str, winner_index_or_none: Optional[int],
) -> bool:
    """
    True when the loser threw the game with a turn-1 CHAOS self-defeat.

    Playing a bank-or-lose card without the abyss cards to pay for it ends the game
    immediately, which makes it the cheapest possible way for two colluding players to
    pump one account's rating. Such a win pays the winner no Elo, and the loser pays a
    punitive multiple of a normal loss (see _apply_one_sided_elo_loss).

    Deliberately turn 1 only: a self-defeat on any later turn rates as an ordinary loss,
    so a pair willing to spend an extra turn cycle can still trade. Every self-defeat is
    written to games.result_summary regardless, so widening this is a data question.

    That scoping is also why none of this is surfaced to players: the boundary is the
    exploitable part, and a pair who can watch the rule fire can find its edge by
    experiment. Nothing here or downstream reaches a channel or a DM.
    """
    if mode != 'standard' or winner_index_or_none is None:
        return False
    if state.self_defeat_turn != 1:
        return False
    # check_win awards a both-at-zero tie to player 0, so the player who self-defeated
    # can in principle still be the winner. Only suppress when they actually lost.
    return state.self_defeat_player == 1 - winner_index_or_none


class SingleMatchFlow:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    # -- runtime -----------------------------------------------------------

    def _ensure_decision_runtime(self, session: 'GameSession') -> None:
        """Install the transport and broker when the caller has not already.
        Solo games seat a model on player 1; run_solo_game builds its own broker
        up front, so this branch is what a resumed solo game relies on (without
        it the bot seat would be prompted by DM at sentinel id 0, never answer,
        and forfeit on consecutive timeouts)."""
        if session.transport is None:
            session.transport = DiscordMatchTransport(self.bot)
        if session.broker is None:
            discord_adapter = DiscordMatchDecisionAdapter(session.transport)
            adapters = {0: discord_adapter, 1: discord_adapter}
            if session.is_solo:
                # Local import: solo_flow imports SingleMatchFlow at module level.
                from zutomayo.match.solo_flow import MODEL_PLAYER_INDEX, create_model_adapter

                adapters[MODEL_PLAYER_INDEX] = create_model_adapter(
                    session, session.solo_difficulty,
                )
            session.broker = MatchDecisionBroker(session, adapters)

    def _player_names(self, session: 'GameSession') -> dict[int, str]:
        self._ensure_decision_runtime(session)
        names = {}
        for discord_id, index in session.player_discord_ids.items():
            names[index] = session.transport.display_name(session, index) or f'Player {index + 1}'
        return names

    async def _send_to_player(self, session: 'GameSession', player_index: int, **kwargs: Any):
        """Transport passthrough kept for the draft phase helpers."""
        self._ensure_decision_runtime(session)
        return await session.transport.send_to_player(session, player_index, **kwargs)

    # -- deck building -----------------------------------------------------

    async def _do_deck_building_phase(
        self, session: 'GameSession',
    ) -> tuple[Optional[list['Card']], Optional[list['Card']]]:
        """Both players choose deck sources simultaneously. None for a player
        means they timed out and receive a random pre-built deck."""
        from zutomayo.data.deck_validator import get_card_index
        from zutomayo.ui.deck_management_views import DeckSourceView

        all_cards, card_index = get_card_index()
        session.clear_pending()
        names = self._player_names(session)

        for index in range(2):
            view = DeckSourceView(
                session=session,
                player_index=index,
                all_cards=all_cards,
                card_index=card_index,
                opponent_name=names[1 - index],
            )
            await session.transport.send_to_player(
                session, index,
                content=(
                    '**Deck Building [デッキ構築]**\n'
                    'Choose how to build your deck:\n'
                    '**Build a Deck** - Enter cards manually\n'
                    '**Select a Deck** - Use one of your saved decks\n'
                    '**Select a Default Deck** - Use a pre-built deck'
                ),
                view=view,
            )

        await session.wait_for_both_players(timeout=750.0)
        return session.pending_actions.get(0), session.pending_actions.get(1)

    # -- entry points --------------------------------------------------------

    async def run_game(self, session: 'GameSession') -> None:
        from zutomayo.engine.game_session import session_manager

        try:
            self._ensure_decision_runtime(session)
            if session.is_draft:
                from zutomayo.match.draft_flow import run_standard_draft_phase

                deck_1_cards, deck_2_cards = await run_standard_draft_phase(self, session)
            else:
                deck_1_cards, deck_2_cards = await self._do_deck_building_phase(session)
            await self.run_single_match(session, deck_1_cards, deck_2_cards)
            await self.finalize_completed_game(session)
            session_manager.remove_game(session.game_id)
        except MatchResumeDivergenceError:
            raise
        except Exception:
            log.exception('Error in game flow')
            await session.transport.send_to_channel(session, content='An error occurred. Game ended.')
            await self.mark_game_abandoned(session)
            session_manager.remove_game(session.game_id)

    async def run_single_match(
        self,
        session: 'GameSession',
        deck_1_cards: Optional[list['Card']],
        deck_2_cards: Optional[list['Card']],
        *,
        record_store: Any = None,
        engine_seed: Optional[int] = None,
        night_player: Optional[int] = None,
    ) -> MatchOutcome:
        """Run one match from engine construction through result recording
        (game-over embed, Elo). Does NOT set the final game status and does
        NOT remove the session - the caller owns the series/game lifecycle.

        ``night_player`` overrides the engine's side coin flip (TCG series
        games after the first, where the previous loser picks their side);
        None keeps the flip."""
        from engine_alpha.game import Game
        from zutomayo.data.deck_validator import get_card_index
        from zutomayo.match.agents import load_random_fallback_deck

        self._ensure_decision_runtime(session)
        _, card_index = get_card_index()

        if deck_1_cards is None:
            deck_1_cards = load_random_fallback_deck(card_index)
            session.player_deck_names[0] = None
        if deck_2_cards is None:
            deck_2_cards = load_random_fallback_deck(card_index)
            session.player_deck_names[1] = None

        decks = (
            definition_indices_for_cards(deck_1_cards),
            definition_indices_for_cards(deck_2_cards),
        )
        game = Game(
            seed=session.random_seed if engine_seed is None else engine_seed,
            mode='fixed_decks', decks=decks,
            night_player=night_player,
        )
        session.game = game
        names = self._player_names(session)

        if record_store is not None:
            session.persistence = record_store
        if session.persistence is None:
            mode = 'solo' if session.is_solo else 'standard'
            session.persistence = await MatchRecordStore.create_for_match(
                session,
                mode,
                engine_seed=session.random_seed,
                deck_card_keys={
                    0: card_keys_for_definition_indices(decks[0]),
                    1: card_keys_for_definition_indices(decks[1]),
                },
            )
        session.broker.persistence = session.persistence

        narrator = self._build_narrator(session)
        driver = EngineMatchDriver(session, game, session.broker, narrator, names)

        await self._announce_game_start(session, names)

        outcome = await driver.run_to_completion()

        if outcome.forfeited_player is not None:
            forfeiter = names.get(outcome.forfeited_player, 'A player')
            await session.transport.send_to_channel(
                session,
                content=f'**{forfeiter}** did not respond in time and forfeits the game.',
            )

        await self._end_game(session, names)
        return outcome

    # -- announcements -------------------------------------------------------

    def _build_narrator(self, session: 'GameSession') -> MatchNarrator:
        """The narrator and its gate presenter render nothing on their own; the
        Discord flow is what hands them the image builders."""
        from zutomayo.ui.board_renderer import (
            generate_zone_messages_off_thread, render_board_image_off_thread,
        )
        from zutomayo.ui.embeds import (
            create_deck_grid_image_off_thread, create_hand_image_off_thread,
        )

        narrator = MatchNarrator(session, session.transport)
        narrator.hand_image_provider = create_hand_image_off_thread
        narrator.reveal_image_provider = create_deck_grid_image_off_thread
        narrator.gate_presenter.board_image_provider = render_board_image_off_thread
        narrator.gate_presenter.zone_messages_provider = generate_zone_messages_off_thread
        return narrator

    async def _announce_game_start(self, session: 'GameSession', names: dict[int, str]) -> None:
        from zutomayo.ui.embeds import build_hand_embed, create_hand_image_off_thread
        from zutomayo.engine import game_events

        board_view = project_board_view(session.game, names)
        if session.persistence is not None:
            for index in range(2):
                session.persistence.emit_event(game_events.EVENT_INITIAL_HAND, {
                    'player_index': index,
                    'cards': [[v.card.pack, v.card.id] for v in board_view.players[index].hand],
                })
        for index in range(2):
            if not session.transport.delivers_to_player(session, index):
                continue
            player_view = board_view.players[index]
            await session.transport.send_to_player(session, index, embed=build_hand_embed(player_view))
            hand_file = await create_hand_image_off_thread(list(player_view.hand))
            if hand_file:
                await session.transport.send_to_player(session, index, files=[hand_file])

    # -- results -------------------------------------------------------------

    async def _end_game(self, session: 'GameSession', names: dict[int, str]) -> None:
        from zutomayo.ui.embeds import build_game_over_embed_from_board_view

        board_view = project_board_view(session.game, names)
        log.info('Game %s ended: winner=%d', session.game_id, board_view.winner)
        if session.persistence is not None:
            await session.persistence.flush_events()

        embed = build_game_over_embed_from_board_view(board_view)
        await session.transport.send_to_channel(session, embed=embed)
        for index in range(2):
            await session.transport.send_to_player(session, index, embed=embed)

        await self._record_match_stats(session)

    async def _record_match_stats(self, session: 'GameSession') -> None:
        from zutomayo.data.player_storage import record_match_result

        # A match completing during restart replay was already recorded before
        # the crash; recording it again would double-count Elo and stats.
        if session.broker is not None and session.broker.replaying:
            return
        try:
            state = session.game.state
            winner = state.winner
            winner_index_or_none = winner if winner in (0, 1) else None
            player_zero_id = session.get_discord_id(0)
            player_one_id = session.get_discord_id(1)
            if player_zero_id is None or player_one_id is None:
                return
            mode = 'tcg_match' if session.is_tcg else 'standard'
            suppress_gain = should_suppress_winner_elo_gain(
                state, mode, winner_index_or_none,
            )
            if suppress_gain:
                log.info(
                    'Game %s: player %d self-defeated on turn 1; winner gains no Elo',
                    session.game_id, state.self_defeat_player,
                )
            await record_match_result(
                player_zero_id,
                player_one_id,
                session.player_deck_names.get(0),
                session.player_deck_names.get(1),
                winner_index_or_none,
                mode=mode,
                is_solo=session.is_solo,
                solo_difficulty=session.solo_difficulty,
                game_id=session.game_id,
                suppress_winner_elo_gain=suppress_gain,
            )
        except Exception:
            log.exception('Failed to record match stats for game %s', session.game_id)

    async def finalize_completed_game(self, session: 'GameSession') -> None:
        from zutomayo.engine.game_persistence import STATUS_COMPLETED

        if session.persistence is None or session.game is None:
            return
        state = session.game.state
        winner = state.winner
        winner_index = winner if winner in (0, 1) else None
        result_name = ('PLAYER_1_WIN', 'PLAYER_2_WIN', 'DRAW')[winner] if winner in (0, 1, 2) else 'IN_PROGRESS'
        result_summary = {'result': result_name, 'turns': state.turn}
        # Recorded for every self-defeat, not just the turn-1 ones the Elo rule acts on,
        # so the question of whether that gate should widen can be answered from data.
        if state.self_defeat_player != -1:
            result_summary['self_defeat_player'] = state.self_defeat_player
            result_summary['self_defeat_turn'] = state.self_defeat_turn
        try:
            await session.persistence.set_status(
                STATUS_COMPLETED,
                winner_index=winner_index,
                result_summary=result_summary,
            )
        except Exception:
            log.exception('Failed to mark game %s completed', session.game_id)

    async def mark_game_abandoned(self, session: 'GameSession') -> None:
        from zutomayo.engine.game_persistence import STATUS_ABANDONED

        if session.persistence is None:
            return
        try:
            await session.persistence.set_status(STATUS_ABANDONED)
        except Exception:
            log.exception('Failed to mark game %s abandoned', session.game_id)
