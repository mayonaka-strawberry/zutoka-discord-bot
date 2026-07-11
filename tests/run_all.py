"""
One-command verification for the whole repository.

Runs, in order:
1. the full pytest suite under coverage,
2. the Tier B flow-transcript comparison under appended coverage (this is
   both a regression gate and the coverage source for the flow modules),
3. per-area coverage gates evaluated from the combined coverage data,
4. the full Tier A engine-transcript comparison.

Usage (from the repository root):
    python tests/run_all.py

Exits nonzero on any test failure, transcript mismatch, or coverage gate miss.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

# The three excluded effect modules are intentionally dead code: their logic
# was inlined into check_area_enchant_removal / should_force_day_attack /
# TurnManager.do_character_swap, which the engine tests cover.
DEAD_EFFECT_MODULES = ('effect_02_005.py', 'effect_02_007.py', 'effect_02_062.py')

# Coverage gates per area. Thresholds sit slightly below measured values so
# ordinary drift does not flake, but any real coverage regression fails.
# Report-only areas (cogs, interactive views, bot model internals, the V2
# training stack) are deliberately ungated: mocked-interaction tests would
# prove the mock, not the bot — manual playtests own those surfaces.
COVERAGE_GATES: list[tuple[str, float, list[str]]] = [
    ('effect handlers (zutomayo/effects/cards + card_effect_helpers)', 99.0, [
        'zutomayo/effects/cards/',
        'zutomayo/effects/card_effect_helpers.py',
    ]),
    ('engine core (effect_engine, turn_manager, game_controller)', 94.0, [
        'zutomayo/effects/effect_engine.py',
        'zutomayo/engine/turn_manager.py',
        'zutomayo/engine/game_controller.py',
    ]),
    ('decision infrastructure (broker, persistence, resume, adapters, transport)', 88.0, [
        'zutomayo/engine/decisions.py',
        'zutomayo/engine/decision_broker.py',
        'zutomayo/engine/game_persistence.py',
        'zutomayo/engine/resume_manager.py',
        'zutomayo/engine/match_transport.py',
        'zutomayo/engine/adapters/',
    ]),
    ('game flows (game_flow, solo, tcg, session)', 83.0, [
        'zutomayo/engine/game_flow.py',
        'zutomayo/engine/solo_game_flow.py',
        'zutomayo/engine/tcg_match_flow.py',
        'zutomayo/engine/game_session.py',
    ]),
    ('data layer (zutomayo/data)', 87.0, ['zutomayo/data/']),
    ('ui core (embeds, renderers)', 75.0, [
        'zutomayo/ui/embeds.py',
        'zutomayo/ui/board_renderer.py',
        'zutomayo/ui/image_utils.py',
        'zutomayo/ui/deck_management_common.py',
    ]),
]


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
            if any(normalized.endswith(dead) for dead in DEAD_EFFECT_MODULES):
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
        PYTHON, '-m', 'coverage', 'run', '--source=zutomayo', '-m', 'pytest', 'tests/', '-q',
    ])
    _run('Tier B flow transcripts under appended coverage', [
        PYTHON, '-m', 'coverage', 'run', '--source=zutomayo', '--append',
        'tests/run_flow_regression.py', 'compare',
    ])
    _run('Export coverage json', [
        PYTHON, '-m', 'coverage', 'json', '-o', str(coverage_json_path),
    ])

    gates_passed = _evaluate_coverage_gates(coverage_json_path)

    _run('Tier A engine transcripts', [
        PYTHON, 'tests/run_engine_regression.py', 'compare',
    ])

    if not gates_passed:
        print('\nRESULT: FAIL (coverage gate)')
        return 1
    print('\nRESULT: PASS (tests, transcripts, and coverage gates)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
