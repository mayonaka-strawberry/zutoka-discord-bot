"""
Match transcript regression harness for the engine_alpha bot stack.

Drives full headless games through the REAL match runtime (MatchDecisionBroker,
presentation, MatchNarrator, EngineMatchDriver) with scripted adapters, a
recording transport, and an in-memory record store, and compares against
golden transcripts. Records per decision: the fingerprint and chosen action;
per apply: narration lines; per game: the final state digest and winner.

Usage:
    python tests/run_match_regression.py write      # regenerate baselines
    python tests/run_match_regression.py compare    # gate: diff against baselines
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

BASELINE_DIRECTORY = REPOSITORY_ROOT / 'tests' / 'baselines' / 'match'
GAME_SEEDS = list(range(24))

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)


def run_one_game(seed: int) -> dict:
    from tests.match.support import (
        FakeSession,
        MemoryRecordStore,
        RecordingTransport,
        ScriptedActionAdapter,
        random_full_pool_decks,
        state_digest,
    )
    from engine_alpha.game import Game
    from zutomayo.match.broker import MatchDecisionBroker
    from zutomayo.match.match_driver import EngineMatchDriver
    from zutomayo.match.narrator import MatchNarrator

    decks = random_full_pool_decks(seed)
    session = FakeSession(game_id=f'REGRESSION-{seed:05d}')
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = ScriptedActionAdapter(lambda: session.broker, seed=seed)
    broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    session.broker = broker
    session.transport = transport
    session.persistence = store
    session.game = Game(seed=seed, mode='fixed_decks', decks=decks)
    narrator = MatchNarrator(session, transport)
    driver = EngineMatchDriver(
        session, session.game, broker, narrator,
        {0: 'Player 1', 1: 'Player 2'},
    )

    outcome = asyncio.run(driver.run_to_completion())

    return {
        'seed': seed,
        'decks': decks,
        'winner': outcome.winner,
        'decisions': [
            {
                'sequence_number': record['sequence_number'],
                'fingerprint': record['fingerprint'],
                'payload': record['payload'],
            }
            for record in store.decisions
        ],
        'events': [
            {'event_type': event['event_type'], 'payload': event['payload']}
            for event in store.events
        ],
        'channel_messages': [
            message.get('content')
            for message in transport.channel_messages
            if message.get('content')
        ],
        'state_digest': repr(state_digest(session.game)),
    }


def transcript_text() -> str:
    lines = []
    for seed in GAME_SEEDS:
        record = run_one_game(seed)
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return '\n'.join(lines) + '\n'


def write_baselines() -> None:
    BASELINE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    text = transcript_text()
    path = BASELINE_DIRECTORY / 'match_games.jsonl.gz'
    path.write_bytes(gzip.compress(text.encode('utf-8'), mtime=0))
    print(f'Wrote {len(GAME_SEEDS)} game transcripts to {path}')


def compare_baselines() -> int:
    path = BASELINE_DIRECTORY / 'match_games.jsonl.gz'
    if not path.exists():
        print(f'FAIL: baseline file {path} missing; run write first')
        return 1
    expected = gzip.decompress(path.read_bytes()).decode('utf-8')
    actual = transcript_text()
    if expected == actual:
        print(f'RESULT: PASS ({len(GAME_SEEDS)} games identical)')
        return 0
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for line_number, (expected_line, actual_line) in enumerate(
            zip(expected_lines, actual_lines)):
        if expected_line != actual_line:
            print(f'FAIL: first divergence at game index {line_number}')
            break
    else:
        print('FAIL: transcript lengths differ')
    print('RESULT: FAIL')
    return 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ('write', 'compare'):
        print(__doc__)
        return 2
    if sys.argv[1] == 'write':
        write_baselines()
        return 0
    return compare_baselines()


if __name__ == '__main__':
    raise SystemExit(main())
