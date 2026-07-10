"""End-to-end resume_all tests: rebuilds sessions from persisted game
records, replays fully-logged games to completion, and marks corrupt or
diverged records divergence_failed without blocking the others."""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.engine.decision_broker import DecisionBroker  # noqa: E402
from zutomayo.engine.game_flow import GameFlow  # noqa: E402
from zutomayo.engine.game_session import GameSession, session_manager  # noqa: E402
from zutomayo.engine.resume_manager import resume_all  # noqa: E402

from tests.fake_adapters import RecordingTransport, ScriptedDecisionAdapter  # noqa: E402
from tests.fakes import InMemoryGameRecordBackend  # noqa: E402
from tests.run_flow_regression import _install_patches  # noqa: E402
from tests.scripted_agents import StatelessScriptedAgent  # noqa: E402
from tests.transcript import TranscriptRecorder  # noqa: E402


def _play_persisted_match(tmp_path) -> InMemoryGameRecordBackend:
    """Play a full 2-player match; the record stays 'active' because
    run_single_match alone never finalizes (run_game / resume do that)."""
    from zutomayo.data.deck_validator import get_card_index
    from tests.run_engine_regression import load_deck_definitions

    record_backend = _install_patches(tmp_path)
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
    assert record_backend.games['resume-e2e']['status'] == 'active'
    return record_backend


def _insert_corrupt_game(record_backend: InMemoryGameRecordBackend) -> None:
    record_backend.games['corrupt-game'] = {
        'game_id': 'corrupt-game',
        'schema_version': 1,
        'status': 'active',
        'mode': 'standard',
        'channel_id': 5,
        'is_solo': False,
        'solo_difficulty': 'normal',
        'is_tcg': False,
        'best_of': 0,
        'random_seed': 1,
        'manifest': {'game_id': 'corrupt-game'},  # missing every required key
        'winner_index': None,
        'result_summary': None,
        'created_at': datetime.now(timezone.utc),
        'saved_at': None,
        'ended_at': None,
    }


def test_resume_all_replays_completed_games_and_finalizes(tmp_path):
    record_backend = _play_persisted_match(tmp_path)

    # A second, corrupt record must not block the resumable one.
    _insert_corrupt_game(record_backend)

    async def run() -> None:
        await resume_all(bot=None)
        # The fully-logged game replays to completion inside its task.
        session = session_manager.active_games.get('resume-e2e')
        assert session is not None and session.game_task is not None
        await asyncio.wait_for(session.game_task, timeout=30)

    asyncio.run(run())

    assert record_backend.games['corrupt-game']['status'] == 'divergence_failed', \
        'corrupt records are marked failed, never deleted'
    assert 'resume-e2e' not in session_manager.active_games
    assert record_backend.games['resume-e2e']['status'] == 'completed'
    assert record_backend.decisions['resume-e2e'], 'the decision log is kept forever'
    assert session_manager.player_to_game.get(111111) is None


def test_resume_all_handles_divergent_logs(tmp_path):
    record_backend = _play_persisted_match(tmp_path)

    record_backend.decisions['resume-e2e'][0]['fingerprint']['option_count'] = 99

    async def run() -> None:
        await resume_all(bot=None)
        session = session_manager.active_games.get('resume-e2e')
        assert session is not None
        await asyncio.wait_for(session.game_task, timeout=30)

    asyncio.run(run())
    assert 'resume-e2e' not in session_manager.active_games
    assert record_backend.games['resume-e2e']['status'] == 'divergence_failed'


def test_resume_all_with_no_active_games_is_a_no_op(tmp_path):
    _install_patches(tmp_path)
    asyncio.run(resume_all(bot=None))
