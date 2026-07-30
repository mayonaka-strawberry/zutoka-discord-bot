"""
One-command verification gate.

Runs, in order:
1. the full pytest suite (engine_alpha tests + bot tests) under coverage,
2. the match transcript regression tier under appended coverage,
3. per-area coverage gates,
4. the match transcript compare (already executed in step 2; its exit code
   gates the run).

Thresholds are set from measured values minus a small flake margin; raise
them when coverage improves, never lower them to pass.

Usage: python tests/run_all.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

# (area name, minimum percent, path prefixes)
COVERAGE_GATES = [
    ('engine_alpha core (game, state, zones, battle, effects, events)', 84.0, [
        'engine_alpha/game.py',
        'engine_alpha/state.py',
        'engine_alpha/zones.py',
        'engine_alpha/battle.py',
        'engine_alpha/actions.py',
        'engine_alpha/events.py',
        'engine_alpha/rng.py',
        'engine_alpha/cards.py',
        'engine_alpha/draft.py',
        'engine_alpha/effects/',
    ]),
    ('match layer (broker, driver, presentation, state view, narrator, persistence)', 90.0, [
        'zutomayo/match/broker.py',
        'zutomayo/match/decisions.py',
        'zutomayo/match/match_driver.py',
        'zutomayo/match/narrator.py',
        'zutomayo/match/persistence.py',
        'zutomayo/match/presentation.py',
        'zutomayo/match/state_view.py',
    ]),
    ('data layer (zutomayo/data)', 87.0, ['zutomayo/data/']),
    ('ui core (embeds, renderers)', 80.0, [
        'zutomayo/ui/embeds.py',
        'zutomayo/ui/board_renderer.py',
        'zutomayo/ui/card_art.py',
        'zutomayo/ui/image_utils.py',
        'zutomayo/ui/deck_management_common.py',
    ]),
]
# Report-only areas (no gate): cogs, Discord views and flows, resume glue,
# model stacks. Their guarantee is the transcript tier plus dev-bot playtests.


def _run(description: str, command: list[str]) -> None:
    print(f'\n=== {description}')
    completed = subprocess.run(command, cwd=REPOSITORY_ROOT)
    if completed.returncode != 0:
        print(f'FAILED: {description}')
        raise SystemExit(completed.returncode)


def _normalize(path_text: str) -> str:
    return path_text.replace('\\', '/')


def _evaluate_coverage_gates(coverage_json_path: Path) -> bool:
    report = json.loads(coverage_json_path.read_text(encoding='utf-8'))
    files = report['files']

    all_passed = True
    print('\n=== Coverage gates')
    for area_name, threshold, prefixes in COVERAGE_GATES:
        covered = 0
        statements = 0
        for file_path, file_report in files.items():
            normalized = _normalize(file_path)
            if not any(prefix in normalized for prefix in prefixes):
                continue
            summary = file_report['summary']
            statements += summary['num_statements']
            covered += summary['covered_lines']
        percent = 100.0 * covered / statements if statements else 100.0
        status = 'PASS' if percent >= threshold else 'FAIL'
        if status == 'FAIL':
            all_passed = False
        print(f'  [{status}] {area_name}: {percent:.1f}% (gate {threshold:.0f}%, {covered}/{statements} statements)')
    return all_passed


def main() -> int:
    coverage_json_path = REPOSITORY_ROOT / 'tests' / 'coverage_summary.json'

    _run('Erase previous coverage data', [PYTHON, '-m', 'coverage', 'erase'])
    _run('Pytest suite under coverage', [
        PYTHON, '-m', 'coverage', 'run', '--source=zutomayo,engine_alpha',
        '-m', 'pytest', 'engine_alpha/tests', 'tests/', '-q',
    ])
    _run('Match transcript regression under appended coverage', [
        PYTHON, '-m', 'coverage', 'run', '--source=zutomayo,engine_alpha', '--append',
        'tests/run_match_regression.py', 'compare',
    ])
    _run('Export coverage json', [
        PYTHON, '-m', 'coverage', 'json', '-o', str(coverage_json_path),
    ])

    gates_passed = _evaluate_coverage_gates(coverage_json_path)

    if not gates_passed:
        print('\nRESULT: FAIL (coverage gate)')
        return 1
    print('\nRESULT: PASS (tests, transcripts, and coverage gates)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
