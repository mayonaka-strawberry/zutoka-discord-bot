"""Game summary renderer tests over real recorded event streams and edge cases."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.data.deck_validator import get_card_index  # noqa: E402
from zutomayo.ui.game_summary_view import build_game_summary  # noqa: E402

from tests.run_flow_regression import _install_patches  # noqa: E402
from tests.test_resume import _attach_runtime, _make_session, _run_entry  # noqa: E402

PLAYER_NAMES = {0: 'Alpha', 1: 'Beta'}


def _play_and_summarize(tmp_path, mode: str):
    record_backend = _install_patches(tmp_path)
    session = _make_session(f'summary-{mode}', mode=mode)
    _attach_runtime(session)
    asyncio.run(_run_entry(session, mode))

    game_row = record_backend.games[session.game_id]
    events = record_backend.events[session.game_id]
    _, card_index = get_card_index()
    return build_game_summary(game_row, PLAYER_NAMES, events, card_index), events


def test_standard_game_summary_pages(tmp_path):
    summary, events = _play_and_summarize(tmp_path, 'standard')

    assert summary.pages, 'a finished game always renders pages'
    overview = summary.pages[0]
    assert 'Alpha' in overview.description and 'Beta' in overview.description
    assert 'standard' in overview.description

    titles = [page.title for page in summary.pages]
    assert any('Opening Hands' in title for title in titles)
    assert any(title.startswith('Turn ') for title in titles)

    all_text = '\n'.join(page.description for page in summary.pages)
    assert 'effect priority' in all_text, 'day/night priority must be visible'
    assert 'opening hand' in all_text
    assert all(len(page.description) <= 4096 for page in summary.pages)

    assert summary.full_log_lines, 'the full log is generated from the same events'
    assert len([line for line in summary.full_log_lines if 'phase_entered' in line]) > 0


def test_tcg_series_summary_shows_swaps_and_score(tmp_path):
    summary, events = _play_and_summarize(tmp_path, 'tcg')

    overview = summary.pages[0]
    assert 'TCG best of 3' in overview.description
    assert 'Series score' in overview.description

    titles = [page.title for page in summary.pages]
    assert any(title.startswith('Match 1') for title in titles)
    assert any(title.startswith('Match 2') for title in titles)

    all_text = '\n'.join(page.description for page in summary.pages)
    swap_happened = any(event['event_type'] == 'side_deck_swap' for event in events)
    assert swap_happened, 'a decided best-of-3 includes at least one swap phase'
    assert 'moved out' in all_text and 'moved in' in all_text
    assert 'won match' in all_text


def test_zero_turn_quit_game_renders_overview_only():
    game_row = {
        'game_id': '20260710-00042',
        'status': 'quit',
        'mode': 'standard',
        'is_tcg': False,
        'is_solo': False,
        'best_of': 0,
        'winner_index': None,
        'result_summary': None,
        'manifest': {'player_deck_names': {'0': 'Alpha Deck', '1': None}},
        'created_at': datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        'ended_at': datetime(2026, 7, 10, 12, 3, tzinfo=timezone.utc),
    }
    _, card_index = get_card_index()
    summary = build_game_summary(game_row, PLAYER_NAMES, [], card_index)

    assert len(summary.pages) == 1
    assert 'quit' in summary.pages[0].description
    assert '**Duration:** 3 minute(s)' in summary.pages[0].description


def test_long_turn_logs_chunk_under_the_embed_limit():
    events = []
    for event_index in range(600):
        events.append({
            'event_index': event_index,
            'match_number': 1,
            'turn': 2,
            'phase': 'PROCESS_EFFECTS',
            'event_type': 'decision_made',
            'payload': {
                'player_index': event_index % 2,
                'kind': 'effect_card_select',
                'purpose': '',
                'prompt_text': 'pick',
                'chosen': [f'A very long option label number {event_index} ' + 'x' * 60],
                'payload_type': 'indices',
            },
        })
    game_row = {
        'game_id': '20260710-00043',
        'status': 'completed',
        'mode': 'standard',
        'is_tcg': False,
        'is_solo': False,
        'best_of': 0,
        'winner_index': 0,
        'result_summary': {'result': 'PLAYER_1_WIN', 'turns': 2},
        'manifest': {'player_deck_names': {}},
        'created_at': datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        'ended_at': datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc),
    }
    _, card_index = get_card_index()
    summary = build_game_summary(game_row, PLAYER_NAMES, events, card_index)

    turn_pages = [page for page in summary.pages if page.title.startswith('Turn 2')]
    assert len(turn_pages) > 1, 'an oversized turn must split into continuation pages'
    assert all(len(page.description) <= 4096 for page in summary.pages)
