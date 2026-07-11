"""Save/resume eligibility and saved-game listing rules (storage level).

The deterministic replay itself is covered by tests/test_resume.py — a save
point is exactly a truncation point. These tests cover the rules around it:
who may resume what, and what the resume/end autocompletes may suggest.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from zutomayo.engine.game_persistence import (  # noqa: E402
    STATUS_SAVED,
    GameRecordStore,
    list_saved_games_for_player,
)
from zutomayo.engine.game_session import GameSession, session_manager  # noqa: E402
from zutomayo.engine.resume_manager import load_saved_game_for_resume  # noqa: E402

PLAYER_ZERO = 111111
PLAYER_ONE = 222222
OUTSIDER = 333333


def _make_session(game_id: str, *, solo: bool = False) -> GameSession:
    session = GameSession(game_id=game_id, channel_id=7, creator_id=PLAYER_ZERO)
    session.add_player(0 if solo else PLAYER_ONE)
    session.is_solo = solo
    session.random_seed = 42
    return session


async def _create_saved_game(game_id: str, *, solo: bool = False) -> GameRecordStore:
    session = _make_session(game_id, solo=solo)
    store = await GameRecordStore.create_for_session(session, 'solo' if solo else 'standard')
    await store.set_status(STATUS_SAVED)
    return store


@pytest.fixture(autouse=True)
def clean_session_manager():
    session_manager.active_games.clear()
    session_manager.player_to_game.clear()
    yield
    session_manager.active_games.clear()
    session_manager.player_to_game.clear()


class TestResumeEligibility:
    def test_missing_game_is_rejected(self):
        with pytest.raises(ValueError, match='not found'):
            asyncio.run(load_saved_game_for_resume('20260710-99999', PLAYER_ZERO))

    def test_non_saved_statuses_are_rejected(self):
        async def run():
            session = _make_session('20260710-00000')
            store = await GameRecordStore.create_for_session(session, 'standard')
            with pytest.raises(ValueError, match='not a saved game'):
                await load_saved_game_for_resume('20260710-00000', PLAYER_ZERO)
            await store.set_status('completed')
            with pytest.raises(ValueError, match='not a saved game'):
                await load_saved_game_for_resume('20260710-00000', PLAYER_ZERO)

        asyncio.run(run())

    def test_non_players_are_rejected(self):
        async def run():
            await _create_saved_game('20260710-00000')
            with pytest.raises(ValueError, match='not a player'):
                await load_saved_game_for_resume('20260710-00000', OUTSIDER)

        asyncio.run(run())

    def test_busy_participants_block_the_resume(self):
        async def run():
            await _create_saved_game('20260710-00000')
            session_manager.player_to_game[PLAYER_ONE] = 'some-other-game'
            with pytest.raises(ValueError, match='another game'):
                await load_saved_game_for_resume('20260710-00000', PLAYER_ZERO)

        asyncio.run(run())

    def test_either_player_of_an_eligible_game_may_request(self):
        async def run():
            await _create_saved_game('20260710-00000')
            row_for_zero = await load_saved_game_for_resume('20260710-00000', PLAYER_ZERO)
            row_for_one = await load_saved_game_for_resume('20260710-00000', PLAYER_ONE)
            return row_for_zero, row_for_one

        row_for_zero, row_for_one = asyncio.run(run())
        assert row_for_zero['game_id'] == row_for_one['game_id'] == '20260710-00000'

    def test_solo_saved_game_ignores_the_bot_sentinel(self):
        async def run():
            await _create_saved_game('20260710-00001', solo=True)
            return await load_saved_game_for_resume('20260710-00001', PLAYER_ZERO)

        assert asyncio.run(run())['is_solo'] is True


class TestSavedGameListing:
    def test_only_own_saved_games_are_listed(self):
        async def run():
            await _create_saved_game('20260710-00000')
            # A completed game must never appear.
            session = _make_session('20260710-00001')
            completed_store = await GameRecordStore.create_for_session(session, 'standard')
            await completed_store.set_status('completed')
            return (
                await list_saved_games_for_player(PLAYER_ZERO),
                await list_saved_games_for_player(OUTSIDER),
            )

        own_games, outsider_games = asyncio.run(run())
        assert [row['game_id'] for row in own_games] == ['20260710-00000']
        assert outsider_games == []

    def test_prefix_filter_applies(self):
        async def run():
            await _create_saved_game('20260710-00000')
            await _create_saved_game('20260711-00000')
            return await list_saved_games_for_player(PLAYER_ZERO, '20260711')

        rows = asyncio.run(run())
        assert [row['game_id'] for row in rows] == ['20260711-00000']

    def test_limit_is_respected(self):
        async def run():
            for counter in range(30):
                await _create_saved_game(f'20260710-{counter:05d}')
            return await list_saved_games_for_player(PLAYER_ZERO, '', limit=25)

        assert len(asyncio.run(run())) == 25


class TestStatusRoundTrip:
    def test_saved_to_active_to_completed(self, install_in_memory_backends):
        async def run():
            store = await _create_saved_game('20260710-00000')
            await store.set_status('active', channel_id=999)
            await store.set_status('completed', winner_index=0,
                                   result_summary={'result': 'PLAYER_1_WIN', 'turns': 5})

        asyncio.run(run())
        game_row = install_in_memory_backends['game_records'].games['20260710-00000']
        assert game_row['status'] == 'completed'
        assert game_row['channel_id'] == 999, 'resume moves the game to the invoking channel'
        assert game_row['saved_at'] is not None and game_row['ended_at'] is not None
