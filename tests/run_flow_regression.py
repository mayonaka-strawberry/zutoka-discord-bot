"""
Tier B flow regression harness.

Runs the REAL GameFlow.run_single_match end to end — decision broker, scripted
adapters (through the production BotAgentDecisionAdapter routing), recording
transport, seeded per-session RNG — and records transcripts of every prompt,
send, effect dispatch, and state digest. Baselines live in tests/baselines/flow/.

Usage (from the repository root):
    python tests/run_flow_regression.py write
    python tests/run_flow_regression.py compare
    python tests/run_flow_regression.py compare --smoke

Coverage grows with the refactor stages: 2-player matches now; TCG series once
the switch phase is broker-routed (Stage 5); solo once the flows merge (Stage 6).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import tempfile
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import random  # noqa: E402

import zutomayo.data.name_storage as name_storage_module  # noqa: E402
import zutomayo.data.player_storage as player_storage_module  # noqa: E402
import zutomayo.engine.game_flow as game_flow_module  # noqa: E402
import zutomayo.engine.game_session as game_session_module  # noqa: E402
from zutomayo.data.card_loader import load_cards  # noqa: E402
from zutomayo.data.deck_validator import build_card_index  # noqa: E402
from zutomayo.effects.effect_engine import EffectEngine, _EFFECT_HANDLERS  # noqa: E402
from zutomayo.engine.decision_broker import DecisionBroker  # noqa: E402
from zutomayo.engine.game_flow import GameFlow  # noqa: E402
from zutomayo.engine.game_session import GameSession  # noqa: E402
from zutomayo.engine.turn_manager import TurnManager  # noqa: E402

from tests.fake_adapters import RecordingTransport, ScriptedDecisionAdapter  # noqa: E402
from tests.run_engine_regression import load_deck_definitions  # noqa: E402
from tests.scripted_agents import ScriptedVarietyAgent  # noqa: E402
from tests.transcript import TranscriptRecorder, card_identity  # noqa: E402

DEFAULT_BASELINE_DIRECTORY = REPOSITORY_ROOT / 'tests' / 'baselines' / 'flow'

PLAYER_ZERO_DISCORD_ID = 111111
PLAYER_ONE_DISCORD_ID = 222222

_ACTIVE_RECORDER: TranscriptRecorder | None = None


class RecordingFlowEffectEngine(EffectEngine):
    """Real EffectEngine plus effect-dispatch and post-effects digests."""

    async def _dispatch(self, game_state, player_index, card_instance):
        effect_id = card_instance.card.effect
        if effect_id in _EFFECT_HANDLERS and _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_effect_dispatch(effect_id, player_index)
        return await super()._dispatch(game_state, player_index, card_instance)

    async def process_effects(self, game_state, player_index):
        result = await super().process_effects(game_state, player_index)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(game_state, f'process_effects_player_{player_index}')
        return result


class RecordingFlowTurnManager(TurnManager):
    def resolve_battle(self, *args, **kwargs):
        result = super().resolve_battle(*args, **kwargs)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(self.game_state, 'resolve_battle')
        return result

    def end_turn(self, player, *args, **kwargs):
        result = super().end_turn(player, *args, **kwargs)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(self.game_state, f'end_turn_player_{player.index}')
        return result


def _make_render_stub(function_name: str, returns_zone_list: bool = False):
    async def render_stub(*args, **kwargs):
        if returns_zone_list:
            game_state, player_names = args[0], args[1]
            if _ACTIVE_RECORDER is not None:
                _ACTIVE_RECORDER.record_render(function_name, [])
            labels = []
            for index in range(2):
                name = player_names.get(index, f'Player {index + 1}')
                labels.append((f'{name} Abyss', 'stub_image'))
                labels.append((f'{name} Power Charger', 'stub_image'))
            return labels
        if _ACTIVE_RECORDER is not None:
            first_argument = args[0] if args else None
            if isinstance(first_argument, list):
                _ACTIVE_RECORDER.record_render(
                    function_name,
                    [card_identity(card_instance) for card_instance in first_argument],
                )
            else:
                _ACTIVE_RECORDER.record_render(function_name, [])
        return 'stub_image'
    return render_stub


def _install_patches(temporary_data_directory: Path) -> 'InMemoryGameRecordBackend':
    # Never write real player profiles, the username cache, or live game
    # records from tests.
    import zutomayo.engine.game_persistence as game_persistence_module

    from tests.fakes import (
        InMemoryGameRecordBackend,
        InMemoryNameBackend,
        InMemoryProfileBackend,
    )

    profile_backend = InMemoryProfileBackend()
    name_backend = InMemoryNameBackend()
    name_backend.profile_backend = profile_backend
    player_storage_module.backend = profile_backend
    name_storage_module.backend = name_backend
    name_storage_module._names_cache = None
    game_record_backend = InMemoryGameRecordBackend()
    game_persistence_module.backend = game_record_backend

    # Digest hooks: GameSession resolves TurnManager from its module namespace.
    game_session_module.TurnManager = RecordingFlowTurnManager
    _install_render_stubs()
    return game_record_backend


def _install_render_stubs() -> None:

    # Rendering stubs: the flow modules bound these names at import time.
    import zutomayo.engine.tcg_match_flow as tcg_match_flow_module

    game_flow_module.render_board_image_off_thread = _make_render_stub('render_board_image')
    game_flow_module.create_hand_image_off_thread = _make_render_stub('create_hand_image')
    game_flow_module.generate_zone_messages_off_thread = _make_render_stub('generate_zone_messages', returns_zone_list=True)
    tcg_match_flow_module.create_deck_grid_image_off_thread = _make_render_stub('create_deck_grid_image')


def _build_recorded_session(recorder: TranscriptRecorder, seed: int, *, solo: bool, tcg: bool, game_id: str) -> GameSession:
    opponent_discord_id = 0 if solo else PLAYER_ONE_DISCORD_ID
    session = GameSession(game_id=game_id, channel_id=987654, creator_id=PLAYER_ZERO_DISCORD_ID)
    session.add_player(opponent_discord_id)
    session.is_solo = solo
    session.is_tcg = tcg
    session.best_of = 3 if tcg else 0
    session.random_seed = seed
    session.random_generator = random.Random(seed)
    session.effect_engine = RecordingFlowEffectEngine()

    session.transport = RecordingTransport(recorder)
    agent_0 = ScriptedVarietyAgent(seed_offset=seed * 2 + 1)
    agent_1 = ScriptedVarietyAgent(seed_offset=seed * 3 + 2)
    session.broker = DecisionBroker(session, {
        0: ScriptedDecisionAdapter(agent_0, recorder),
        1: ScriptedDecisionAdapter(agent_1, recorder),
    })
    return session


def play_recorded_match(deck_cards_0, deck_cards_1, seed: int, *, solo: bool = False) -> TranscriptRecorder:
    global _ACTIVE_RECORDER
    recorder = TranscriptRecorder()
    _ACTIVE_RECORDER = recorder
    try:
        session = _build_recorded_session(
            recorder, seed, solo=solo, tcg=False, game_id=f'flow-seed-{seed}',
        )
        flow = GameFlow(bot=None)
        winner = asyncio.run(flow.run_single_match(session, deck_cards_0, deck_cards_1))
        recorder.record_state_digest(session.game_state, 'final')
        recorder.record({
            'event': 'match_result',
            'winner': winner,
            'turns': session.game_state.turn,
        })
    except Exception as error:
        import traceback
        traceback.print_exc()
        recorder.record({
            'event': 'game_error',
            'error_type': type(error).__name__,
            'error_message': str(error),
        })
    finally:
        _ACTIVE_RECORDER = None
    return recorder


def play_recorded_tcg_series(deck_0, side_0, deck_1, side_1, seed: int) -> TranscriptRecorder:
    from zutomayo.engine.tcg_match_flow import TcgMatchFlow

    global _ACTIVE_RECORDER
    recorder = TranscriptRecorder()
    _ACTIVE_RECORDER = recorder
    try:
        session = _build_recorded_session(
            recorder, seed, solo=False, tcg=True, game_id=f'tcg-seed-{seed}',
        )
        flow = TcgMatchFlow(bot=None, best_of=3)
        asyncio.run(flow.run_tcg(
            session,
            resumed_decks=(list(deck_0), list(side_0), list(deck_1), list(side_1)),
        ))
        recorder.record_state_digest(session.game_state, 'final')
    except Exception as error:
        import traceback
        traceback.print_exc()
        recorder.record({
            'event': 'game_error',
            'error_type': type(error).__name__,
            'error_message': str(error),
        })
    finally:
        _ACTIVE_RECORDER = None
    return recorder


def build_match_matrix() -> list[tuple[str, str, str, int, str]]:
    """Entries: (file_stem, slug_0, slug_1, seed, game_kind)."""
    from tests.run_engine_regression import build_synthetic_deck_definitions

    named = [slug for slug, _ in load_deck_definitions() if slug.startswith('default')]
    matrix = []
    pair_index = 0
    for first in range(len(named)):
        for second in range(first, len(named)):
            seed = 50_000 + pair_index * 37
            matrix.append((f'{named[first]}_vs_{named[second]}_seed{seed}', named[first], named[second], seed, 'standard'))
            pair_index += 1
    synthetic = [slug for slug, _ in build_synthetic_deck_definitions()][:8]
    for index in range(0, len(synthetic) - 1, 2):
        seed = 70_000 + index * 53
        matrix.append((
            f'{synthetic[index]}_vs_{synthetic[index + 1]}_seed{seed}',
            synthetic[index], synthetic[index + 1], seed, 'standard',
        ))

    # Solo matches through the merged flow (bot answers via the production
    # BotAgentDecisionAdapter routing; transport skips the bot's DMs/renders).
    for solo_number in range(6):
        slug_0 = named[solo_number % len(named)]
        slug_1 = named[(solo_number + 2) % len(named)]
        seed = 80_000 + solo_number * 61
        matrix.append((f'solo_{slug_0}_vs_{slug_1}_seed{seed}', slug_0, slug_1, seed, 'solo'))

    # TCG best-of-3 series with scripted switch phases. The side deck is the
    # first eight cards of a third deck.
    for series_number in range(3):
        slug_0 = named[series_number % len(named)]
        slug_1 = named[(series_number + 3) % len(named)]
        seed = 90_000 + series_number * 71
        matrix.append((f'tcg_{slug_0}_vs_{slug_1}_seed{seed}', slug_0, slug_1, seed, 'tcg'))
    return matrix


def _side_deck_for(slug: str, deck_entries_by_slug, card_index) -> list:
    """Deterministic 8-card side deck: first 8 entries of the named deck."""
    entries = deck_entries_by_slug[slug][:8]
    return [card_index[(entry['pack'], entry['id'])] for entry in entries]


def run(mode: str, baseline_directory: Path, limit: int | None) -> int:
    from tests.run_engine_regression import build_synthetic_deck_definitions

    with tempfile.TemporaryDirectory() as temporary_data_directory:
        _install_patches(Path(temporary_data_directory))

        card_index = build_card_index(load_cards())
        deck_entries_by_slug = dict(load_deck_definitions() + build_synthetic_deck_definitions())
        matrix = build_match_matrix()
        if limit is not None:
            matrix = matrix[:limit]

        print(f'{mode}: {len(matrix)} matches against {baseline_directory}')
        start_time = time.monotonic()

        mismatched: list[str] = []
        missing: list[str] = []
        generated_names: set[str] = set()

        if mode == 'write':
            baseline_directory.mkdir(parents=True, exist_ok=True)
            for stale in baseline_directory.glob('*.jsonl.gz'):
                stale.unlink()

        for match_number, (file_stem, slug_0, slug_1, seed, game_kind) in enumerate(matrix, start=1):
            deck_cards_0 = [card_index[(entry['pack'], entry['id'])] for entry in deck_entries_by_slug[slug_0]]
            deck_cards_1 = [card_index[(entry['pack'], entry['id'])] for entry in deck_entries_by_slug[slug_1]]
            if game_kind == 'tcg':
                side_0 = _side_deck_for('default05', deck_entries_by_slug, card_index)
                side_1 = _side_deck_for('default06', deck_entries_by_slug, card_index)
                recorder = play_recorded_tcg_series(deck_cards_0, side_0, deck_cards_1, side_1, seed)
            else:
                recorder = play_recorded_match(
                    deck_cards_0, deck_cards_1, seed, solo=(game_kind == 'solo'),
                )
            transcript_text = recorder.to_jsonl()
            file_name = f'{file_stem}.jsonl.gz'
            generated_names.add(file_name)
            baseline_path = baseline_directory / file_name

            if mode == 'write':
                baseline_path.write_bytes(gzip.compress(transcript_text.encode('utf-8'), mtime=0))
            else:
                if not baseline_path.exists():
                    missing.append(file_name)
                elif gzip.decompress(baseline_path.read_bytes()).decode('utf-8') != transcript_text:
                    mismatched.append(file_name)

            if match_number % 10 == 0 or match_number == len(matrix):
                elapsed = time.monotonic() - start_time
                print(f'  {match_number}/{len(matrix)} matches, {elapsed:.1f}s elapsed')

        if mode == 'write':
            print(f'Baselines written: {len(matrix)} transcripts.')
            return 0

        if limit is None:
            stale = sorted(
                path.name for path in baseline_directory.glob('*.jsonl.gz')
                if path.name not in generated_names
            )
        else:
            stale = []
        failed = bool(mismatched or missing or stale)
        if mismatched:
            print(f'MISMATCHED ({len(mismatched)}): {mismatched[:15]}')
        if missing:
            print(f'MISSING BASELINES ({len(missing)}): {missing[:10]}')
        if stale:
            print(f'STALE BASELINES ({len(stale)}): {stale[:10]}')
        print('RESULT: ' + ('FAIL' if failed else 'PASS'))
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Tier B flow regression harness.')
    parser.add_argument('mode', choices=('write', 'compare'))
    parser.add_argument('--baseline-directory', type=Path, default=DEFAULT_BASELINE_DIRECTORY)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--smoke', action='store_true', help='Shortcut for --limit 3.')
    arguments = parser.parse_args()
    limit = 3 if arguments.smoke else arguments.limit
    return run(arguments.mode, arguments.baseline_directory, limit)


if __name__ == '__main__':
    raise SystemExit(main())
