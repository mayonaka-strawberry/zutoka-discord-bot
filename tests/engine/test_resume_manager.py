"""End-to-end resume_all tests: rebuilds sessions from persisted directories,
replays fully-logged games to completion, and cleans up corrupt or diverged
directories without blocking the others."""

from __future__ import annotations

import asyncio
import json
import random
import shutil
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import zutomayo.engine.game_persistence as game_persistence_module  # noqa: E402
from zutomayo.engine.decision_broker import DecisionBroker  # noqa: E402
from zutomayo.engine.game_flow import GameFlow  # noqa: E402
from zutomayo.engine.game_session import GameSession, session_manager  # noqa: E402
from zutomayo.engine.resume_manager import resume_all  # noqa: E402

from tests.fake_adapters import RecordingTransport, ScriptedDecisionAdapter  # noqa: E402
from tests.run_flow_regression import _install_patches  # noqa: E402
from tests.scripted_agents import StatelessScriptedAgent  # noqa: E402
from tests.support.effect_harness import card_identities  # noqa: E402
from tests.transcript import TranscriptRecorder  # noqa: E402


def _play_persisted_match(tmp_path) -> Path:
    """Play a full 2-player match with persistence and KEEP the directory."""
    from zutomayo.data.deck_validator import get_card_index
    from tests.run_engine_regression import load_deck_definitions

    _install_patches(tmp_path)
    _, card_index = get_card_index()
    definitions = dict(load_deck_definitions())
    deck_0 = [card_index[(entry['pack'], entry['id'])] for entry in definitions['default00']]
    deck_1 = [card_index[(entry['pack'], entry['id'])] for entry in definitions['default01']]

    session = GameSession(game_id='resume-e2e', channel_id=5, creator_id=111111)
    session.add_player(222222)
    session.random_seed = 777
    session.random_generator = random.Random(777)
    recorder = TranscriptRecorder()
    session.transport = RecordingTransport(recorder)
    session.broker = DecisionBroker(session, {
        0: ScriptedDecisionAdapter(StatelessScriptedAgent(), recorder),
        1: ScriptedDecisionAdapter(StatelessScriptedAgent(), recorder),
    })
    asyncio.run(GameFlow(bot=None).run_single_match(session, deck_0, deck_1))
    return session.persistence.game_directory


def test_resume_all_replays_completed_games_and_cleans_up(tmp_path):
    game_directory = _play_persisted_match(tmp_path)
    assert game_directory.exists()

    # A second, corrupt directory must not block the resumable one.
    corrupt_directory = game_directory.parent / 'corrupt-game'
    corrupt_directory.mkdir()
    (corrupt_directory / 'manifest.json').write_text('{not json', encoding='utf-8')

    async def run() -> None:
        await resume_all(bot=None)
        # The fully-logged game replays to completion inside its task.
        session = session_manager.active_games.get('resume-e2e')
        assert session is not None and session.game_task is not None
        await asyncio.wait_for(session.game_task, timeout=30)

    asyncio.run(run())

    assert not corrupt_directory.exists(), 'corrupt directories are deleted'
    assert 'resume-e2e' not in session_manager.active_games
    assert not game_directory.exists(), 'a finished replay removes its persistence'
    assert session_manager.player_to_game.get(111111) is None


def test_resume_all_handles_divergent_logs(tmp_path):
    game_directory = _play_persisted_match(tmp_path)

    decisions_path = game_directory / 'decisions.jsonl'
    lines = decisions_path.read_text(encoding='utf-8').strip().split('\n')
    tampered = json.loads(lines[0])
    tampered['fingerprint']['option_count'] = 99
    lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    decisions_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    async def run() -> None:
        await resume_all(bot=None)
        session = session_manager.active_games.get('resume-e2e')
        assert session is not None
        await asyncio.wait_for(session.game_task, timeout=30)

    asyncio.run(run())
    assert 'resume-e2e' not in session_manager.active_games
    assert not game_directory.exists(), 'diverged games are ended and cleaned up'


def test_resume_all_with_no_directories_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(game_persistence_module, 'ACTIVE_GAMES_DIRECTORY', tmp_path / 'empty')
    asyncio.run(resume_all(bot=None))