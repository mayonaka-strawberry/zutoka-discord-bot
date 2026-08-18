"""Where /zutomayo resume may be used, and where its confirmation is delivered.

Resume works from a DM or a server channel. The channel only carries public
narration - play itself is always over DM - so the invoking context decides how
a two-player confirmation reaches the opponent, and whether the game moves:
resuming from a server channel relocates it there, resuming from a DM leaves it
on its recorded channel (0 for solo games, meaning nothing is posted publicly).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

import zutomayo.match.resume as resume_module
import zutomayo.match.transport as transport_module
from zutomayo.cogs.game_cog import GameCog
from zutomayo.engine.game_persistence import STATUS_ACTIVE, STATUS_SAVED
from zutomayo.engine.game_session import GameSession, session_manager
from zutomayo.ui.resume_views import ResumeConfirmationView

PLAYER_ZERO = 111111
PLAYER_ONE = 222222
RECORDED_CHANNEL_ID = 7
INVOKING_CHANNEL_ID = 555
GAME_ID = '20260710-00000'


# -- fakes ---------------------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.deferred = False

    async def send_message(self, content=None, **kwargs) -> None:
        self.messages.append({'content': content, **kwargs})

    async def defer(self, **kwargs) -> None:
        self.deferred = True

    async def edit_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.messages.append({'content': content, **kwargs})
        return SimpleNamespace(id=1)


class FakeInteraction:
    def __init__(self, user_id: int = PLAYER_ZERO, in_guild: bool = True) -> None:
        self.guild = object() if in_guild else None
        self.channel_id = INVOKING_CHANNEL_ID if in_guild else None
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.user = SimpleNamespace(
            id=user_id, display_name='Tester', global_name='Tester', name='tester', bot=False,
        )

    async def original_response(self):
        return SimpleNamespace(id=99)


class FakeDMChannel:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.messages.append({'content': content, **kwargs})
        return SimpleNamespace(id=42)


def build_cog() -> GameCog:
    cog = GameCog.__new__(GameCog)
    cog.bot = SimpleNamespace(get_user=lambda user_id: None)
    return cog


def closed_dms_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=403, reason='Forbidden'), 'closed')


# -- fixtures and helpers ------------------------------------------------


@pytest.fixture(autouse=True)
def clean_session_manager():
    session_manager.active_games.clear()
    session_manager.player_to_game.clear()
    yield
    session_manager.active_games.clear()
    session_manager.player_to_game.clear()


@pytest.fixture
def recorded_resumes(monkeypatch) -> list[dict]:
    """Capture resume_game calls instead of rebuilding and replaying a match."""
    calls: list[dict] = []

    async def fake_resume_game(bot, game_id, *, channel_id_override=None, announcement=None):
        calls.append({
            'game_id': game_id,
            'channel_id_override': channel_id_override,
            'announcement': announcement,
        })
        return None

    monkeypatch.setattr(resume_module, 'resume_game', fake_resume_game)
    return calls


@pytest.fixture
def opponent_dm(monkeypatch) -> tuple[FakeDMChannel, list[int]]:
    """Intercept the DM the resume request is delivered through."""
    dm_channel = FakeDMChannel()
    opened_for: list[int] = []

    async def fake_open_dm_channel(bot, discord_id):
        opened_for.append(discord_id)
        return dm_channel

    monkeypatch.setattr(transport_module, 'open_dm_channel', fake_open_dm_channel)
    return dm_channel, opened_for


async def _create_saved_game(*, solo: bool = False) -> None:
    from zutomayo.match.persistence import MatchRecordStore

    session = GameSession(
        game_id=GAME_ID,
        channel_id=0 if solo else RECORDED_CHANNEL_ID,
        creator_id=PLAYER_ZERO,
    )
    session.add_player(0 if solo else PLAYER_ONE)
    session.is_solo = solo
    session.random_seed = 42
    store = await MatchRecordStore.create_for_match(
        session, 'solo' if solo else 'standard',
        engine_seed=session.random_seed, deck_card_keys={},
    )
    await store.set_status(STATUS_SAVED)
    session_manager.active_games.clear()
    session_manager.player_to_game.clear()


def run_resume(interaction: FakeInteraction, *, solo: bool = False) -> None:
    async def run():
        await _create_saved_game(solo=solo)
        await GameCog.resume_saved_game.callback(build_cog(), interaction, GAME_ID)

    asyncio.run(run())


def game_row(backends) -> dict:
    return backends['game_records'].games[GAME_ID]


# -- solo resume ---------------------------------------------------------


class TestSoloResume:
    """Solo games play out entirely over DM, so the invoking context is
    irrelevant and the game never moves off its recorded channel 0."""

    @pytest.mark.parametrize('in_guild', [True, False])
    def test_resumes_from_either_context_without_relocating(self, recorded_resumes, in_guild):
        interaction = FakeInteraction(in_guild=in_guild)
        run_resume(interaction, solo=True)

        assert 'Resuming game' in interaction.response.messages[0]['content']
        assert recorded_resumes == [{
            'game_id': GAME_ID,
            'channel_id_override': 0,
            'announcement': '**Game resumed.**',
        }]

    def test_unavailable_opponent_leaves_the_game_saved(
        self, monkeypatch, install_in_memory_backends,
    ):
        async def failing_resume_game(bot, game_id, **kwargs):
            raise ValueError('Model A is not available.')

        monkeypatch.setattr(resume_module, 'resume_game', failing_resume_game)
        interaction = FakeInteraction(in_guild=False)
        run_resume(interaction, solo=True)

        assert 'not available' in interaction.followup.messages[0]['content']
        assert game_row(install_in_memory_backends)['status'] == STATUS_SAVED


# -- two-player resume from a server channel -----------------------------


class TestTwoPlayerResumeFromGuild:
    def test_posts_the_confirmation_in_the_invoking_channel(self):
        interaction = FakeInteraction(in_guild=True)
        run_resume(interaction)

        sent = interaction.response.messages[0]
        assert f'<@{PLAYER_ONE}>' in sent['content']
        assert isinstance(sent['view'], ResumeConfirmationView)
        assert interaction.followup.messages == []

    def test_accepting_relocates_the_game_to_that_channel(
        self, recorded_resumes, install_in_memory_backends,
    ):
        interaction = FakeInteraction(in_guild=True)
        run_resume(interaction)
        view = interaction.response.messages[0]['view']

        asyncio.run(view.on_accept(FakeInteraction(user_id=PLAYER_ONE)))

        assert recorded_resumes[0]['channel_id_override'] == INVOKING_CHANNEL_ID
        assert game_row(install_in_memory_backends)['channel_id'] == INVOKING_CHANNEL_ID


# -- two-player resume from a DM -----------------------------------------


class TestTwoPlayerResumeFromDirectMessage:
    def test_delivers_the_confirmation_to_the_opponent_dm(self, opponent_dm):
        dm_channel, opened_for = opponent_dm
        interaction = FakeInteraction(in_guild=False)
        run_resume(interaction)

        assert opened_for == [PLAYER_ONE], 'the request goes to the opponent, not the invoker'
        assert interaction.response.deferred, 'opening a DM needs more than the 3s window'
        assert isinstance(dm_channel.messages[0]['view'], ResumeConfirmationView)
        assert 'sent to' in interaction.followup.messages[0]['content']

    def test_accepting_keeps_the_game_on_its_recorded_channel(
        self, opponent_dm, recorded_resumes, install_in_memory_backends,
    ):
        dm_channel, _ = opponent_dm
        run_resume(FakeInteraction(in_guild=False))
        view = dm_channel.messages[0]['view']

        asyncio.run(view.on_accept(FakeInteraction(user_id=PLAYER_ONE)))

        assert recorded_resumes[0]['channel_id_override'] == RECORDED_CHANNEL_ID
        assert game_row(install_in_memory_backends)['channel_id'] == RECORDED_CHANNEL_ID
        assert game_row(install_in_memory_backends)['status'] == STATUS_ACTIVE

    def test_closed_dms_leave_the_game_saved(self, monkeypatch, install_in_memory_backends):
        async def refusing_open_dm_channel(bot, discord_id):
            raise closed_dms_error()

        monkeypatch.setattr(transport_module, 'open_dm_channel', refusing_open_dm_channel)
        interaction = FakeInteraction(in_guild=False)
        run_resume(interaction)

        assert 'could not send' in interaction.followup.messages[0]['content']
        assert game_row(install_in_memory_backends)['status'] == STATUS_SAVED


# -- confirmation view shape ---------------------------------------------


class TestResumeConfirmationView:
    def _build(self, allow_cancel: bool) -> ResumeConfirmationView:
        async def on_accept(interaction) -> None:
            return None

        return ResumeConfirmationView(
            game_id=GAME_ID, invoker_id=PLAYER_ZERO, opponent_id=PLAYER_ONE,
            on_accept=on_accept, allow_cancel=allow_cancel,
        )

    def test_channel_requests_keep_cancel(self):
        labels = [item.label for item in self._build(True).children]
        assert labels == ['Accept and Resume', 'Decline', 'Cancel']

    def test_dm_requests_drop_the_unpressable_cancel(self):
        labels = [item.label for item in self._build(False).children]
        assert labels == ['Accept and Resume', 'Decline']
