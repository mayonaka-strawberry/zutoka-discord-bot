"""
End-to-end replay-resume tests.

An uninterrupted 2-player match is played through the real GameFlow with
persistence enabled and stateless scripted agents. The persisted directory is
then copied with the decision log truncated at various points (simulating a
crash), and the game is resumed the way the resume manager does it: session
rebuilt from the manifest, broker preloaded with the log, transport muted.
Because agents are stateless and every shuffle draws from the seeded session
generator, the resumed game must reach the exact same final state and winner.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import random  # noqa: E402

import zutomayo.engine.game_persistence as game_persistence_module  # noqa: E402
from zutomayo.data.card_loader import load_cards  # noqa: E402
from zutomayo.data.deck_validator import build_card_index  # noqa: E402
from zutomayo.engine.decision_broker import DecisionBroker, ResumeDivergenceError  # noqa: E402
from zutomayo.engine.game_flow import GameFlow  # noqa: E402
from zutomayo.engine.game_persistence import (  # noqa: E402
    load_decision_log,
    load_manifest,
    resolve_card_keys,
)
from zutomayo.engine.game_session import GameSession  # noqa: E402
from zutomayo.engine.resume_manager import _rebuild_session  # noqa: E402

from tests.fake_adapters import RecordingTransport, ScriptedDecisionAdapter  # noqa: E402
from tests.run_engine_regression import load_deck_definitions  # noqa: E402
from tests.run_flow_regression import _install_patches  # noqa: E402
from tests.scripted_agents import StatelessScriptedAgent  # noqa: E402
from tests.transcript import TranscriptRecorder  # noqa: E402

MATCH_SEED = 424242
PLAYER_ZERO_ID = 111111
PLAYER_ONE_ID = 222222


def _decks():
    card_index = build_card_index(load_cards())
    definitions = dict(load_deck_definitions())
    deck_0 = [card_index[(entry['pack'], entry['id'])] for entry in definitions['default00']]
    deck_1 = [card_index[(entry['pack'], entry['id'])] for entry in definitions['default01']]
    return deck_0, deck_1


def _side_decks():
    card_index = build_card_index(load_cards())
    definitions = dict(load_deck_definitions())
    side_0 = [card_index[(entry['pack'], entry['id'])] for entry in definitions['default02'][:8]]
    side_1 = [card_index[(entry['pack'], entry['id'])] for entry in definitions['default03'][:8]]
    return side_0, side_1


def _final_digest(session: GameSession) -> dict:
    recorder = TranscriptRecorder()
    recorder.record_state_digest(session.game_state, 'final')
    return recorder.events[-1]


def _attach_runtime(session: GameSession) -> TranscriptRecorder:
    recorder = TranscriptRecorder()
    session.transport = RecordingTransport(recorder)
    session.broker = DecisionBroker(session, {
        0: ScriptedDecisionAdapter(StatelessScriptedAgent(), recorder),
        1: ScriptedDecisionAdapter(StatelessScriptedAgent(), recorder),
    })
    return recorder


def _make_session(game_id: str, *, mode: str) -> GameSession:
    opponent_id = 0 if mode == 'solo' else PLAYER_ONE_ID
    session = GameSession(game_id=game_id, channel_id=7, creator_id=PLAYER_ZERO_ID)
    session.add_player(opponent_id)
    session.is_solo = mode == 'solo'
    session.is_tcg = mode == 'tcg'
    session.best_of = 3 if mode == 'tcg' else 0
    session.random_seed = MATCH_SEED
    session.random_generator = random.Random(MATCH_SEED)
    return session


async def _run_entry(session: GameSession, mode: str, manifest: dict | None = None):
    """Run the mode's match entry; returns the winner for single matches."""
    card_index = build_card_index(load_cards())
    if manifest is not None:
        deck_0 = resolve_card_keys(manifest['deck_0'], card_index)
        deck_1 = resolve_card_keys(manifest['deck_1'], card_index)
    else:
        deck_0, deck_1 = _decks()

    if mode == 'tcg':
        from zutomayo.engine.tcg_match_flow import TcgMatchFlow

        if manifest is not None:
            side_0 = resolve_card_keys(manifest['side_0'], card_index)
            side_1 = resolve_card_keys(manifest['side_1'], card_index)
        else:
            side_0, side_1 = _side_decks()
        flow = TcgMatchFlow(bot=None, best_of=3)
        await flow.run_tcg(session, resumed_decks=(deck_0, side_0, deck_1, side_1))
        return None
    flow = GameFlow(bot=None)
    return await flow.run_single_match(session, deck_0, deck_1)


def _play_original(tmp_path: Path, mode: str = 'standard') -> tuple[dict, int | None, Path]:
    """Play the uninterrupted game; returns (final digest, winner, game directory)."""
    _install_patches(tmp_path)
    session = _make_session(f'resume-original-{mode}', mode=mode)
    _attach_runtime(session)

    winner = asyncio.run(_run_entry(session, mode))

    game_directory = session.persistence.game_directory
    assert game_directory.exists(), f'persistence must be created for a {mode} game'
    assert (game_directory / 'decisions.jsonl').exists()
    return _final_digest(session), winner, game_directory


def _resume_from(game_directory: Path, tmp_path: Path, truncate_to: int | None, mode: str = 'standard') -> tuple[dict, int | None, TranscriptRecorder, GameSession]:
    """Copy the persisted game (optionally truncating the log) and resume it."""
    resumed_directory = tmp_path / f'resumed_{mode}_{truncate_to}'
    shutil.copytree(game_directory, resumed_directory)
    if truncate_to is not None:
        decisions_path = resumed_directory / 'decisions.jsonl'
        lines = decisions_path.read_text(encoding='utf-8').strip().split('\n')
        kept = lines[:truncate_to]
        decisions_path.write_text('\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8')

    manifest = load_manifest(resumed_directory)
    session = _rebuild_session(manifest)
    recorder = _attach_runtime(session)

    session.persistence = game_persistence_module.GamePersistence.attach_for_resume(resumed_directory)
    session.broker.persistence = session.persistence
    session.broker.replay_log = load_decision_log(resumed_directory)
    session.broker.replaying = True
    session.transport.muted = True
    went_live = []

    async def on_go_live():
        session.transport.muted = False
        went_live.append(True)

    session.broker.on_go_live = on_go_live

    winner = asyncio.run(_run_entry(session, mode, manifest=manifest))
    recorder.went_live = bool(went_live)  # type: ignore[attr-defined]
    return _final_digest(session), winner, recorder, session


def _assert_resume_reproduces(tmp_path, mode: str) -> None:
    original_digest, original_winner, game_directory = _play_original(tmp_path, mode)

    total_decisions = len(
        (game_directory / 'decisions.jsonl').read_text(encoding='utf-8').strip().split('\n')
    )
    assert total_decisions >= 6, f'the {mode} test game must involve several decisions'

    truncation_points = sorted({
        0,                          # nothing logged: full re-play live from move zero
        2,                          # mid-redraw / initial battle card
        total_decisions // 2,       # mid-game
        total_decisions - 1,        # crash just before the last decision
        None,                       # full log: game completes entirely from replay
    }, key=lambda value: (value is None, value))

    for truncate_to in truncation_points:
        resumed_digest, resumed_winner, recorder, _ = _resume_from(game_directory, tmp_path, truncate_to, mode)
        assert resumed_winner == original_winner, f'{mode}: winner differs at truncation {truncate_to}'
        assert resumed_digest == original_digest, f'{mode}: final state differs at truncation {truncate_to}'
        if truncate_to is None:
            assert not recorder.went_live, f'{mode}: a fully logged game must finish inside replay'
        else:
            assert recorder.went_live, f'{mode}: truncated log {truncate_to} must go live'


def test_resume_reproduces_final_state_at_many_truncation_points(tmp_path):
    _assert_resume_reproduces(tmp_path, 'standard')


def test_solo_resume_reproduces_final_state(tmp_path):
    _assert_resume_reproduces(tmp_path, 'solo')


def test_tcg_series_resume_reproduces_final_state(tmp_path):
    _assert_resume_reproduces(tmp_path, 'tcg')


def test_fingerprint_divergence_raises(tmp_path):
    original_digest, original_winner, game_directory = _play_original(tmp_path)

    diverged_directory = tmp_path / 'diverged'
    shutil.copytree(game_directory, diverged_directory)
    decisions_path = diverged_directory / 'decisions.jsonl'
    lines = decisions_path.read_text(encoding='utf-8').strip().split('\n')
    first_record = json.loads(lines[0])
    first_record['fingerprint']['option_count'] = 99
    lines[0] = json.dumps(first_record, ensure_ascii=False, sort_keys=True)
    decisions_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    manifest = load_manifest(diverged_directory)
    session = _rebuild_session(manifest)
    _attach_runtime(session)
    session.broker.replay_log = load_decision_log(diverged_directory)
    session.broker.replaying = True
    session.transport.muted = True

    card_index = build_card_index(load_cards())
    deck_0 = resolve_card_keys(manifest['deck_0'], card_index)
    deck_1 = resolve_card_keys(manifest['deck_1'], card_index)

    flow = GameFlow(bot=None)
    try:
        asyncio.run(flow.run_single_match(session, deck_0, deck_1))
    except ResumeDivergenceError:
        pass
    else:
        raise AssertionError('expected ResumeDivergenceError')
