"""Unit tests for DiscordMatchTransport (offline-testable parts), GameSession
synchronization, and the session manager."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from zutomayo.engine.game_session import GameSession, GameSessionManager  # noqa: E402
from zutomayo.engine.match_transport import DiscordMatchTransport  # noqa: E402


def _two_player_session() -> GameSession:
    session = GameSession(game_id='transport-test', channel_id=1, creator_id=111)
    session.add_player(222)
    return session


def _solo_session() -> GameSession:
    session = GameSession(game_id='transport-solo', channel_id=1, creator_id=111)
    session.add_player(0)
    session.is_solo = True
    return session


class TestDiscordMatchTransport:
    def test_muted_transport_sends_nothing(self):
        transport = DiscordMatchTransport(bot=None)
        transport.muted = True
        session = _two_player_session()
        assert asyncio.run(transport.send_to_player(session, 0, content='x')) is None
        assert asyncio.run(transport.send_to_channel(session, content='x')) is None

    def test_bot_sentinel_receives_no_dms(self):
        transport = DiscordMatchTransport(bot=None)
        session = _solo_session()
        assert asyncio.run(transport.send_to_player(session, 1, content='x')) is None

    def test_delivers_to_player(self):
        transport = DiscordMatchTransport(bot=None)
        solo = _solo_session()
        assert transport.delivers_to_player(solo, 0) is True
        assert transport.delivers_to_player(solo, 1) is False
        transport.muted = True
        assert transport.delivers_to_player(solo, 0) is False

    def test_display_name_resolves_bot_and_humans(self):
        from zutomayo.data.name_storage import remember_user
        from zutomayo.engine.bot_agent import BOT_NAME

        transport = DiscordMatchTransport(bot=None)
        solo = _solo_session()
        remember_user(111, 'HumanPlayer')
        assert transport.display_name(solo, 0) == 'HumanPlayer'
        assert transport.display_name(solo, 1) == BOT_NAME

        empty_session = GameSession(game_id='half', channel_id=1, creator_id=111)
        assert transport.display_name(empty_session, 1) is None


class TestGameSessionSynchronization:
    def test_submit_wait_and_clear(self):
        session = _two_player_session()

        async def run() -> bool:
            session.submit_action(0, 'move')
            received = await session.wait_for_player(0, timeout=0.05)
            assert received and session.pending_actions[0] == 'move'

            timed_out = await session.wait_for_player(1, timeout=0.01)
            assert timed_out is False

            session.submit_action(1, 'other')
            both = await session.wait_for_both_players(timeout=0.05)
            assert both is True

            session.clear_pending_player(0)
            assert 0 not in session.pending_actions
            session.clear_pending()
            assert session.pending_actions == {}
            return True

        assert asyncio.run(run())

    def test_player_index_lookup(self):
        session = _two_player_session()
        assert session.get_player_index(111) == 0
        assert session.get_player_index(222) == 1
        assert session.get_discord_id(0) == 111
        assert session.get_discord_id(1) == 222
        assert session.get_discord_id(5) is None
        assert session.is_full


class TestGameSessionManager:
    def test_create_join_and_remove(self, tmp_path):
        manager = GameSessionManager()
        session = manager.create_game(channel_id=9, creator_id=111)
        assert manager.get_session_by_player(111) is session

        with pytest.raises(ValueError):
            manager.create_game(channel_id=9, creator_id=111)
        with pytest.raises(ValueError):
            manager.join_game(session.game_id, 111)
        with pytest.raises(ValueError):
            manager.join_game('missing', 333)

        manager.join_game(session.game_id, 222)
        assert session.is_full
        with pytest.raises(ValueError):
            manager.join_game(session.game_id, 333)

        manager.remove_game(session.game_id)
        assert manager.get_session_by_player(111) is None

    def test_remove_game_deletes_persistence(self, tmp_path, monkeypatch):
        import zutomayo.engine.game_persistence as game_persistence_module
        from zutomayo.engine.game_persistence import GamePersistence

        monkeypatch.setattr(game_persistence_module, 'ACTIVE_GAMES_DIRECTORY', tmp_path / 'active')
        manager = GameSessionManager()
        session = manager.create_game(channel_id=9, creator_id=111)
        manager.join_game(session.game_id, 222)
        session.persistence = GamePersistence.create_for_session(session, 'standard')
        game_directory = session.persistence.game_directory
        assert game_directory.exists()

        manager.remove_game(session.game_id)
        assert not game_directory.exists()

    def test_create_solo_game_wires_the_bot_player(self):
        manager = GameSessionManager()
        session = manager.create_solo_game(channel_id=9, creator_id=111)
        assert session.is_solo and session.get_discord_id(1) == 0