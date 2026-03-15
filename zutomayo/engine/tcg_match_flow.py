"""
TCG best-of-N match orchestrator.

Wraps GameFlow to run multiple single matches with a card-switching
phase between them. The series continues until one player reaches
the required number of wins.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
import discord
from zutomayo.engine.game_flow import GameFlow
from zutomayo.engine.game_session import session_manager
from zutomayo.ui.deck_management_views_tcg import TcgDeckSourceView, _random_tcg_deck
from zutomayo.ui.embeds import create_deck_grid_image
from zutomayo.ui.tcg_switch_views import SwitchCardsView

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card


log = logging.getLogger(__name__)


class TcgMatchFlow:
    def __init__(self, bot: discord.Client, best_of: int) -> None:
        self.bot = bot
        self.best_of = best_of
        self.wins_needed = (best_of // 2) + 1
        self.game_flow = GameFlow(bot)

    def _player_names(self, session: GameSession) -> dict[int, str]:
        return self.game_flow._player_names(session)

    async def _send_to_channel(self, session: GameSession, **kwargs) -> None:
        await self.game_flow._send_to_channel(session, **kwargs)

    async def _send_to_player(self, session: GameSession, player_index: int, **kwargs) -> None:
        await self.game_flow._send_to_player(session, player_index, **kwargs)

    async def _send_to_both(self, session: GameSession, **kwargs) -> None:
        await self.game_flow._send_to_both(session, **kwargs)

    async def run_tcg(self, session: GameSession) -> None:
        """Main TCG best-of-N match loop."""
        try:
            wins = {0: 0, 1: 0}
            match_number = 0
            names = self._player_names(session)

            # Phase 1: TCG deck selection
            decks = await self._do_tcg_deck_selection(session)
            deck_0, side_0, deck_1, side_1 = decks

            while wins[0] < self.wins_needed and wins[1] < self.wins_needed:
                match_number += 1

                # Announce match start
                await self._announce_match_start(session, names, match_number, wins)

                # Run single match
                winner = await self.game_flow.run_single_match(session, deck_0, deck_1)

                if winner is None:
                    # Draw: replay with same decks (no switch step)
                    await self._announce_draw(session, names, match_number)
                    match_number -= 1  # Don't count this match
                    continue

                wins[winner] += 1

                # Announce match result
                await self._announce_match_result(session, names, match_number, wins, winner)

                # Check if series is over
                if wins[0] >= self.wins_needed or wins[1] >= self.wins_needed:
                    break

                # Switch cards phase
                deck_0, side_0, deck_1, side_1 = await self._do_switch_cards(
                    session, names, deck_0, side_0, deck_1, side_1,
                )

            # Announce series winner
            await self._announce_series_result(session, names, wins)
            session_manager.remove_game(session.game_id)

        except Exception:
            log.exception('Error in TCG match flow')
            await self._send_to_channel(session, content='An error occurred. TCG series ended.')
            session_manager.remove_game(session.game_id)

    async def _do_tcg_deck_selection(
        self, session: GameSession,
    ) -> tuple[list[Card], list[Card], list[Card], list[Card]]:
        """Run deck selection for both players. Returns (deck_0, side_0, deck_1, side_1)."""
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_validator import build_card_index

        all_cards = load_cards()
        card_index = build_card_index(all_cards)
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
            await self._send_to_player(
                session, index,
                content=(
                    '**TCG Deck Building [デッキ構築]**\n'
                    'Choose how to build your deck:\n'
                    '**Build a Deck** - Enter cards manually or get a random deck\n'
                    '**Select a Deck** - Use one of your saved TCG decks'
                ),
                view=view,
            )

        await session.wait_for_both_players(timeout=600.0)

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
        self, session: GameSession, player_index: int, deck: list[Card], side: list[Card],
    ) -> None:
        """DM a player images of their main deck and side deck."""
        main_img = create_deck_grid_image(deck, columns=5, filename='main_deck.webp')
        side_img = create_deck_grid_image(side, columns=4, filename='side_deck.webp')
        if main_img:
            await self._send_to_player(session, player_index, content='**Main Deck (20):**', file=main_img)
        if side_img:
            await self._send_to_player(session, player_index, content='**Side Deck (8):**', file=side_img)

    async def _do_switch_cards(
        self,
        session: GameSession,
        names: dict[int, str],
        deck_0: list[Card],
        side_0: list[Card],
        deck_1: list[Card],
        side_1: list[Card],
    ) -> tuple[list[Card], list[Card], list[Card], list[Card]]:
        """Run card switching phase for both players between matches."""
        session.clear_pending()

        for index, (deck, side) in enumerate([(deck_0, side_0), (deck_1, side_1)]):
            # Send current deck images before switching
            await self._send_deck_images(session, index, deck, side)

            view = SwitchCardsView(
                session=session,
                player_index=index,
                main_deck=list(deck),
                side_deck=list(side),
                opponent_name=names[1 - index],
            )
            await self._send_to_player(
                session, index,
                content='**Switch Cards [サイドデッキの入れ替え]**\nSwap cards between your main deck and side deck.',
                view=view,
            )

        await session.wait_for_both_players(timeout=300.0)

        # Apply swaps for each player
        all_decks = [(deck_0, side_0), (deck_1, side_1)]
        swap_counts = []

        for index in range(2):
            deck, side = all_decks[index]
            action = session.pending_actions.get(index, {'removed': [], 'added': []})
            removed = action['removed']
            added = action['added']

            # Remove cards from main deck, add to side
            for card in removed:
                deck.remove(card)
                side.append(card)
            # Remove cards from side, add to main
            for card in added:
                side.remove(card)
                deck.append(card)

            swap_counts.append(len(removed))

        # Send updated deck images after switching
        for index, (deck, side) in enumerate([(deck_0, side_0), (deck_1, side_1)]):
            await self._send_deck_images(session, index, deck, side)

        # Announce swap counts to channel and both DMs
        msg_lines = []
        for index in range(2):
            msg_lines.append(f'**{names[index]}** swapped **{swap_counts[index]}** card(s).')
        swap_msg = '\n'.join(msg_lines)

        await self._send_to_channel(session, content=swap_msg)
        await self._send_to_both(session, content=swap_msg)

        return deck_0, side_0, deck_1, side_1

    async def _announce_match_start(
        self, session: GameSession, names: dict[int, str], match_number: int, wins: dict[int, int],
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
        session: GameSession,
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
        self, session: GameSession, names: dict[int, str], match_number: int,
    ) -> None:
        embed = discord.Embed(
            title=f'Match {match_number} — Draw!',
            description='The match ended in a draw. Replaying with the same decks...',
            color=discord.Color.gold(),
        )
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)

    async def _announce_series_result(
        self, session: GameSession, names: dict[int, str], wins: dict[int, int],
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
