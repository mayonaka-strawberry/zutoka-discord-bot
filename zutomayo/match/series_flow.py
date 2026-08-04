"""
TcgSeriesFlow: TCG best-of-N series on the engine_alpha state machine.

Wraps SingleMatchFlow to run multiple matches with a side-deck switching
phase between them. One persistence record covers the whole series: the
initial decks and sides in the manifest, every engine action and switch
decision in one globally sequenced log. Each game's engine seed is derived
from the persisted series seed and a per-game counter (draws replay with a
fresh seed but identical derivation, so restart replay walks the same
sequence).

Match 1 leaves the day/night sides to the engine's coin flip. After each
decided match the loser picks the side they play next, so the choice is a
logged broker decision and replays deterministically; a drawn match carries
the current sides' chooser over untouched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import discord

from zutomayo.match.broker import MatchResumeDivergenceError
from zutomayo.match.decisions import (
    KIND_SIDE_CHOICE,
    KIND_SIDE_DECK_SWITCH,
    PAYLOAD_CARD_KEYS,
    SIDE_ACTION_DAY,
    SIDE_ACTION_NIGHT,
    SIDE_LABEL_DAY,
    SIDE_LABEL_NIGHT,
    MatchDecisionOption,
    MatchDecisionRequest,
)
from zutomayo.match.match_flow import SingleMatchFlow
from zutomayo.match.persistence import MatchRecordStore

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card

log = logging.getLogger(__name__)


class TcgSeriesFlow:
    def __init__(self, bot: discord.Client, best_of: int) -> None:
        self.bot = bot
        self.best_of = best_of
        self.wins_needed = (best_of // 2) + 1
        self.match_flow = SingleMatchFlow(bot)

    def _player_names(self, session: 'GameSession') -> dict[int, str]:
        return self.match_flow._player_names(session)

    async def _send_to_channel(self, session: 'GameSession', **kwargs) -> None:
        await session.transport.send_to_channel(session, **kwargs)

    async def _send_to_both(self, session: 'GameSession', **kwargs) -> None:
        for index in range(2):
            await session.transport.send_to_player(session, index, **kwargs)

    @staticmethod
    def _emit_event(session: 'GameSession', event_type: str, payload: dict, **context) -> None:
        if session.persistence is not None:
            session.persistence.emit_event(event_type, payload, **context)

    async def run_tcg(
        self,
        session: 'GameSession',
        resumed_decks: tuple[list['Card'], list['Card'], list['Card'], list['Card']] | None = None,
    ) -> None:
        from zutomayo.engine.game_session import session_manager

        try:
            self.match_flow._ensure_decision_runtime(session)
            wins = {0: 0, 1: 0}
            match_number = 0
            game_counter = 0
            # None = let the engine flip for sides (match 1, and any replay of
            # a drawn match 1). From match 2 on this holds the side chosen by
            # the previous match's loser; a draw carries it over untouched.
            night_player: int | None = None
            names = self._player_names(session)

            if resumed_decks is not None:
                deck_0, side_0, deck_1, side_1 = resumed_decks
            elif session.is_draft:
                from zutomayo.match.draft_flow import run_tcg_draft_phase

                deck_0, side_0, deck_1, side_1 = await run_tcg_draft_phase(self.match_flow, session)
            else:
                deck_0, side_0, deck_1, side_1 = await self._do_tcg_deck_selection(session)

            from zutomayo.engine.game_persistence import card_keys

            if session.persistence is None:
                session.persistence = await MatchRecordStore.create_for_match(
                    session, 'tcg',
                    engine_seed=session.random_seed,
                    deck_card_keys={},
                    extra_fields={
                        'deck_0': card_keys(deck_0),
                        'deck_1': card_keys(deck_1),
                        'side_0': card_keys(side_0),
                        'side_1': card_keys(side_1),
                    },
                )
            session.broker.persistence = session.persistence

            from zutomayo.engine.game_events import (
                EVENT_MATCH_RESULT,
                EVENT_MATCH_START,
                EVENT_SERIES_RESULT,
                EVENT_SERIES_START,
            )

            self._emit_event(session, EVENT_SERIES_START, {
                'best_of': self.best_of,
                'deck_names': {str(index): session.player_deck_names.get(index) for index in range(2)},
                'decks': {
                    '0': {'main': card_keys(deck_0), 'side': card_keys(side_0)},
                    '1': {'main': card_keys(deck_1), 'side': card_keys(side_1)},
                },
            })

            while wins[0] < self.wins_needed and wins[1] < self.wins_needed:
                match_number += 1
                game_counter += 1

                self._emit_event(session, EVENT_MATCH_START, {
                    'series_score': [wins[0], wins[1]],
                    'decks': {
                        '0': {'main': card_keys(deck_0), 'side': card_keys(side_0)},
                        '1': {'main': card_keys(deck_1), 'side': card_keys(side_1)},
                    },
                }, match_number=match_number)

                await self._announce_match_start(session, names, match_number, wins)

                outcome = await self.match_flow.run_single_match(
                    session, deck_0, deck_1,
                    engine_seed=self._match_seed(session, game_counter),
                    night_player=night_player,
                )
                winner = outcome.winner

                if winner == 2:
                    await self._announce_draw(session, names, match_number)
                    match_number -= 1
                    continue

                wins[winner] += 1

                self._emit_event(session, EVENT_MATCH_RESULT, {
                    'match_number': match_number,
                    'winner_index': winner,
                    'series_score': [wins[0], wins[1]],
                })

                await self._announce_match_result(session, names, match_number, wins, winner)

                if wins[0] >= self.wins_needed or wins[1] >= self.wins_needed:
                    break

                deck_0, side_0, deck_1, side_1 = await self._do_switch_cards(
                    session, names, deck_0, side_0, deck_1, side_1,
                )
                night_player = await self._do_side_choice(
                    session, names, chooser_index=1 - winner, match_number=match_number,
                )

            self._emit_event(session, EVENT_SERIES_RESULT, {
                'score': [wins[0], wins[1]],
                'winner_index': 0 if wins[0] >= self.wins_needed else 1,
            })

            await self._announce_series_result(session, names, wins)
            await self._record_series_stats(session, wins)
            await self._finalize_completed_series(session, wins)
            session_manager.remove_game(session.game_id)

        except MatchResumeDivergenceError:
            # Handled by the resume manager (apology message, no forfeit).
            raise
        except Exception:
            log.exception('Error in TCG series flow')
            await self._send_to_channel(session, content='An error occurred. TCG series ended.')
            await self.match_flow.mark_game_abandoned(session)
            session_manager.remove_game(session.game_id)

    @staticmethod
    def _match_seed(session: 'GameSession', game_counter: int) -> int:
        from engine_alpha.rng import derive_seed

        return derive_seed(session.random_seed, game_counter)

    async def _finalize_completed_series(self, session: 'GameSession', wins: dict[int, int]) -> None:
        from zutomayo.engine.game_persistence import STATUS_COMPLETED

        if session.persistence is None:
            return
        series_winner = 0 if wins[0] >= self.wins_needed else 1
        try:
            await session.persistence.set_status(
                STATUS_COMPLETED,
                winner_index=series_winner,
                result_summary={'series_score': [wins[0], wins[1]], 'best_of': self.best_of},
            )
        except Exception:
            log.exception('Failed to mark TCG series %s completed', session.game_id)

    @staticmethod
    def _cards_for_keys(pool: list['Card'], keys: list[list[int]]) -> list['Card']:
        """Resolve [pack, id] pairs against a card pool, honoring duplicates."""
        remaining = list(pool)
        chosen: list['Card'] = []
        for pack, card_id in keys:
            for card in remaining:
                if card.pack == pack and card.id == card_id:
                    chosen.append(card)
                    remaining.remove(card)
                    break
        return chosen

    async def _record_series_stats(self, session: 'GameSession', wins: dict[int, int]) -> None:
        from zutomayo.data.player_storage import record_tcg_series

        if session.broker is not None and session.broker.replaying:
            return
        try:
            player_zero_id = session.get_discord_id(0)
            player_one_id = session.get_discord_id(1)
            if player_zero_id is None or player_one_id is None:
                return
            await record_tcg_series(player_zero_id, player_one_id, wins, game_id=session.game_id)
        except Exception:
            log.exception('Failed to record TCG series stats for game %s', session.game_id)

    async def _do_tcg_deck_selection(
        self, session: 'GameSession',
    ) -> tuple[list['Card'], list['Card'], list['Card'], list['Card']]:
        from zutomayo.data.deck_validator import get_card_index
        from zutomayo.ui.deck_management_views_tcg import TcgDeckSourceView, _random_tcg_deck

        all_cards, card_index = get_card_index()
        session.clear_pending()
        names = self._player_names(session)

        for index in range(2):
            view = TcgDeckSourceView(
                session=session,
                player_index=index,
                all_cards=all_cards,
                card_index=card_index,
                opponent_name=names[1 - index],
            )
            await session.transport.send_to_player(
                session, index,
                content=(
                    '**TCG Deck Building [デッキ構築]**\n'
                    'Choose how to build your deck:\n'
                    '**Build a Deck** - Enter cards manually or get a random deck\n'
                    '**Select a Deck** - Use one of your saved TCG decks'
                ),
                view=view,
            )

        await session.wait_for_both_players(timeout=750.0)

        action_0 = session.pending_actions.get(0)
        action_1 = session.pending_actions.get(1)

        if action_0 is None:
            main_0, side_0 = _random_tcg_deck(all_cards)
        else:
            main_0, side_0 = action_0['deck'], action_0['side_deck']

        if action_1 is None:
            main_1, side_1 = _random_tcg_deck(all_cards)
        else:
            main_1, side_1 = action_1['deck'], action_1['side_deck']

        return main_0, side_0, main_1, side_1

    async def _send_deck_images(
        self, session: 'GameSession', player_index: int, deck: list['Card'], side: list['Card'],
    ) -> None:
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        main_img = await create_deck_grid_image_off_thread(deck, columns=5, filename='main_deck.jpg')
        side_img = await create_deck_grid_image_off_thread(side, columns=4, filename='side_deck.jpg')
        if main_img:
            await session.transport.send_to_player(
                session, player_index, content='**Main Deck (20):**', file=main_img)
        if side_img:
            await session.transport.send_to_player(
                session, player_index, content='**Side Deck (8):**', file=side_img)

    async def _do_switch_cards(
        self,
        session: 'GameSession',
        names: dict[int, str],
        deck_0: list['Card'],
        side_0: list['Card'],
        deck_1: list['Card'],
        side_1: list['Card'],
    ) -> tuple[list['Card'], list['Card'], list['Card'], list['Card']]:
        session.clear_pending()

        all_decks = [(deck_0, side_0), (deck_1, side_1)]
        requests = []
        for index, (deck, side) in enumerate(all_decks):
            await self._send_deck_images(session, index, deck, side)
            requests.append(MatchDecisionRequest(
                kind=KIND_SIDE_DECK_SWITCH,
                player_index=index,
                prompt_text='**Switch Cards [サイドデッキの入れ替え]**\nSwap cards between your main deck and side deck.',
                timeout_seconds=750.0,
                opponent_name=names[1 - index],
                live_objects={'main_deck': list(deck), 'side_deck': list(side)},
            ))

        responses = await asyncio.gather(
            *(session.broker.request(request) for request in requests)
        )

        swap_counts = []
        for index in range(2):
            deck, side = all_decks[index]
            response = responses[index]
            if response.payload_type == PAYLOAD_CARD_KEYS and response.payload:
                removed_keys = response.payload.get('removed', [])
                added_keys = response.payload.get('added', [])
            else:
                removed_keys, added_keys = [], []

            for card in self._cards_for_keys(deck, removed_keys):
                deck.remove(card)
                side.append(card)
            added = self._cards_for_keys(side, added_keys)
            for card in added:
                side.remove(card)
                deck.append(card)

            swap_counts.append(len(removed_keys))

            from zutomayo.engine.game_events import EVENT_SIDE_DECK_SWAP

            self._emit_event(session, EVENT_SIDE_DECK_SWAP, {
                'player_index': index,
                'removed': removed_keys,
                'added': added_keys,
            })

        for index, (deck, side) in enumerate([(deck_0, side_0), (deck_1, side_1)]):
            await self._send_deck_images(session, index, deck, side)

        message_lines = []
        for index in range(2):
            message_lines.append(f'**{names[index]}** swapped **{swap_counts[index]}** card(s).')
        swap_message = '\n'.join(message_lines)

        await self._send_to_channel(session, content=swap_message)
        await self._send_to_both(session, content=swap_message)

        return deck_0, side_0, deck_1, side_1

    @staticmethod
    def _night_player_for_choice(chooser_index: int, action: int) -> int:
        """The player index on the NIGHT side once `chooser_index` has picked."""
        if action == SIDE_ACTION_NIGHT:
            return chooser_index
        return 1 - chooser_index

    async def _do_side_choice(
        self,
        session: 'GameSession',
        names: dict[int, str],
        chooser_index: int,
        match_number: int,
    ) -> int:
        """The previous match's loser picks the side they play in the next
        match. Returns the player index that sits on the NIGHT side."""
        prompt_text = (
            '**Choose Your Side [昼夜の選択]**\n'
            f'You lost Match {match_number}, so you choose which side you play '
            f'in Match {match_number + 1}.\n'
            f'{SIDE_LABEL_DAY}: your opponent sets cards first on turn 1 and after a tied battle.\n'
            f'{SIDE_LABEL_NIGHT}: you set cards first on turn 1 and after a tied battle.'
        )
        request = MatchDecisionRequest(
            kind=KIND_SIDE_CHOICE,
            player_index=chooser_index,
            prompt_text=prompt_text,
            options=[
                MatchDecisionOption(
                    label=SIDE_LABEL_DAY,
                    description='Your opponent sets cards first on turn 1 and after a tied battle.',
                    action=SIDE_ACTION_DAY,
                ),
                MatchDecisionOption(
                    label=SIDE_LABEL_NIGHT,
                    description='You set cards first on turn 1 and after a tied battle.',
                    action=SIDE_ACTION_NIGHT,
                ),
            ],
            timeout_seconds=750.0,
            opponent_name=names[1 - chooser_index],
        )
        response = await session.broker.request(request)
        night_player = self._night_player_for_choice(chooser_index, response.payload)
        chose_night = night_player == chooser_index

        from zutomayo.engine.game_events import EVENT_SIDE_CHOICE

        self._emit_event(session, EVENT_SIDE_CHOICE, {
            'chooser_index': chooser_index,
            'night_player': night_player,
            'chose_night': chose_night,
            'timed_out': response.timed_out,
        })

        chosen_label = SIDE_LABEL_NIGHT if chose_night else SIDE_LABEL_DAY
        announcement = (
            f'**{names[chooser_index]}** lost Match {match_number} and chose to play '
            f'**{chosen_label}**.\n'
            f'**{names[night_player]}** plays {SIDE_LABEL_NIGHT}; '
            f'**{names[1 - night_player]}** plays {SIDE_LABEL_DAY}.'
        )
        await self._send_to_channel(session, content=announcement)
        await self._send_to_both(session, content=announcement)
        return night_player

    async def _announce_match_start(
        self, session: 'GameSession', names: dict[int, str], match_number: int, wins: dict[int, int],
    ) -> None:
        embed = discord.Embed(
            title=f'TCG Match {match_number} — Best of {self.best_of}',
            description=f'{names[0]} **{wins[0]} - {wins[1]}** {names[1]}',
            color=discord.Color.blue(),
        )
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)

    async def _announce_match_result(
        self,
        session: 'GameSession',
        names: dict[int, str],
        match_number: int,
        wins: dict[int, int],
        winner: int,
    ) -> None:
        embed = discord.Embed(
            title=f'Match {match_number} Result',
            description=(
                f'**{names[winner]}** wins match {match_number}!\n'
                f'**Series score:** {names[0]} **{wins[0]} - {wins[1]}** {names[1]}'
            ),
            color=discord.Color.green(),
        )
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)

    async def _announce_draw(
        self, session: 'GameSession', names: dict[int, str], match_number: int,
    ) -> None:
        embed = discord.Embed(
            title=f'Match {match_number} — Draw!',
            description='The match ended in a draw. Replaying with the same decks...',
            color=discord.Color.gold(),
        )
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)

    async def _announce_series_result(
        self, session: 'GameSession', names: dict[int, str], wins: dict[int, int],
    ) -> None:
        series_winner = 0 if wins[0] >= self.wins_needed else 1
        embed = discord.Embed(
            title=f'TCG Series Complete — Best of {self.best_of}',
            description=(
                f'**{names[series_winner]}** wins the series!\n'
                f'**Final score:** {names[0]} **{wins[0]} - {wins[1]}** {names[1]}'
            ),
            color=discord.Color.gold(),
        )
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)
        log.info(
            'TCG series %s ended: %s %d - %d %s',
            session.game_id, names[0], wins[0], wins[1], names[1],
        )
