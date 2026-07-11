"""Recent-game history listing and the profile embed for other players."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.engine.game_persistence import (  # noqa: E402
    GameRecordStore,
    list_recent_games_for_player,
)
from zutomayo.engine.game_session import GameSession  # noqa: E402
from zutomayo.ui.player_embeds import build_profile_embed  # noqa: E402

PLAYER_ZERO = 111111
PLAYER_ONE = 222222
OUTSIDER = 333333


async def _create_game(game_id: str, status: str, *, winner_index=None, solo: bool = False):
    session = GameSession(game_id=game_id, channel_id=7, creator_id=PLAYER_ZERO)
    session.add_player(0 if solo else PLAYER_ONE)
    session.is_solo = solo
    session.random_seed = 42
    store = await GameRecordStore.create_for_session(session, 'solo' if solo else 'standard')
    await store.set_status(status, winner_index=winner_index)


class TestRecentGameListing:
    def test_only_finished_games_of_the_player_are_listed(self):
        async def run():
            await _create_game('20260710-00000', 'completed', winner_index=0)
            await _create_game('20260710-00001', 'quit')
            await _create_game('20260710-00002', 'saved')
            await _create_game('20260710-00003', 'active')
            return await list_recent_games_for_player(PLAYER_ZERO)

        rows = asyncio.run(run())
        assert {row['game_id'] for row in rows} == {'20260710-00000', '20260710-00001'}

    def test_rows_carry_seat_and_opponent_for_outcome_rendering(self):
        async def run():
            await _create_game('20260710-00000', 'completed', winner_index=1)
            await _create_game('20260710-00001', 'completed', winner_index=0, solo=True)
            return (
                await list_recent_games_for_player(PLAYER_ONE),
                await list_recent_games_for_player(PLAYER_ZERO),
            )

        player_one_rows, player_zero_rows = asyncio.run(run())
        pvp_row = player_one_rows[0]
        assert pvp_row['player_index'] == 1
        assert pvp_row['winner_index'] == 1, 'player one can see they won'
        assert pvp_row['opponent_discord_id'] == PLAYER_ZERO

        solo_row = next(row for row in player_zero_rows if row['game_id'] == '20260710-00001')
        assert solo_row['opponent_discord_id'] == 0, 'solo opponent is the bot sentinel'

    def test_outsiders_see_nothing(self):
        async def run():
            await _create_game('20260710-00000', 'completed', winner_index=0)
            return await list_recent_games_for_player(OUTSIDER)

        assert asyncio.run(run()) == []


class TestProfileEmbedForOthers:
    def test_own_and_other_titles_differ(self):
        empty_profile = {'stats': {}, 'deck_stats': {}, 'opponent_stats': {}}
        own = build_profile_embed(None, empty_profile, display_name='Alpha', viewing_own=True)
        other = build_profile_embed(None, empty_profile, display_name='Beta', viewing_own=False)
        assert own.title == 'Your Profile — Alpha'
        assert other.title == 'Profile — Beta'
        assert 'playuniguri' in own.description
        assert 'playuniguri' not in other.description

    def test_missing_avatar_is_tolerated(self):
        profile = {
            'stats': {'standard': {'wins': 1, 'losses': 0, 'draws': 0}},
            'deck_stats': {}, 'opponent_stats': {},
        }
        embed = build_profile_embed(
            None, profile, display_name='Beta', avatar_url=None, viewing_own=False,
        )
        assert embed.thumbnail.url is None
