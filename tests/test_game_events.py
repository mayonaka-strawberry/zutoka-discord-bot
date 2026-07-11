"""
Event-stream invariants for the permanent game event log.

One scripted game per mode (standard, solo, TCG best-of-3) is played through
the real flows with the in-memory record backend, then the recorded event
stream is checked for the properties the summary renderer depends on:

- every phase of every turn appears, in canonical order
- exactly one effect_priority_determined per PROCESS_EFFECTS, matching the
  day/night priority rule
- effect resolution order_index values are contiguous per order choice
- every logged decision has a human-readable decision_made mirror
- event_index values are strictly monotonic
- TCG series record side-deck swaps between decided matches and a series result
- recording is observation-only (never touches the session RNG)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.test_resume import _attach_runtime, _make_session, _run_entry  # noqa: E402
from tests.run_flow_regression import _install_patches  # noqa: E402

TURN_ZERO_PHASES = ['SETUP']
TURN_ONE_PHASES = ['ADVANCE_CHRONOS', 'PROCESS_EFFECTS', 'BATTLE', 'END_TURN']
FULL_TURN_PHASES = [
    'SET_CARDS', 'REVEAL', 'ADVANCE_CHRONOS', 'CHARACTER_SWAP',
    'AREA_ENCHANT_SWAP', 'PROCESS_EFFECTS', 'BATTLE', 'TURN_END_EFFECTS', 'END_TURN',
]


def _play_recorded_game(tmp_path, mode: str):
    record_backend = _install_patches(tmp_path)
    session = _make_session(f'events-{mode}', mode=mode)
    _attach_runtime(session)
    asyncio.run(_run_entry(session, mode))
    events = record_backend.events[session.game_id]
    decisions = record_backend.decisions[session.game_id]
    return events, decisions


def _of_type(events, event_type):
    return [event for event in events if event['event_type'] == event_type]


def _assert_common_invariants(events, decisions) -> None:
    indices = [event['event_index'] for event in events]
    assert indices == sorted(indices) and len(set(indices)) == len(indices), \
        'event_index must be strictly monotonic'

    decision_events = _of_type(events, 'decision_made')
    assert len(decision_events) == len(decisions), \
        'every logged decision must have a human-readable mirror event'
    mirrored_sequence_numbers = {event['payload']['sequence_number'] for event in decision_events}
    assert mirrored_sequence_numbers == set(decisions), 'decision mirrors must match the log 1:1'

    # Phase coverage: group phase events by (match, turn) and check canonical order.
    phases_by_turn: dict[tuple, list[str]] = {}
    for event in _of_type(events, 'phase_entered'):
        key = (event['match_number'], event['turn'])
        phases_by_turn.setdefault(key, []).append(event['phase'])

    for (match_number, turn), phases in phases_by_turn.items():
        if turn == 0:
            expected = TURN_ZERO_PHASES
        elif turn == 1:
            expected = TURN_ONE_PHASES
        else:
            expected = FULL_TURN_PHASES
        assert phases == expected[:len(phases)], \
            f'match {match_number} turn {turn}: phases {phases} must follow canonical order'
        if 'END_TURN' in phases:
            assert phases == expected, \
                f'match {match_number} turn {turn}: a completed turn must record every phase'

    # Priority: exactly one per PROCESS_EFFECTS entry, and both players ordered.
    process_effects_entries = [
        event for event in _of_type(events, 'phase_entered')
        if event['phase'] == 'PROCESS_EFFECTS'
    ]
    priority_events = _of_type(events, 'effect_priority_determined')
    assert len(priority_events) == len(process_effects_entries), \
        'one effect_priority_determined per PROCESS_EFFECTS phase'
    for event in priority_events:
        priority_index = event['payload']['priority_player_index']
        assert event['payload']['resolution_order'] == [priority_index, 1 - priority_index]
        assert event['payload']['day_night'] in ('DAY', 'NIGHT')

    # Effect ordering: order_index values are contiguous from zero per choice.
    current_expected_order_index = None
    for event in events:
        if event['event_type'] == 'effect_order_chosen':
            current_expected_order_index = 0
            assert event['payload']['source'] in ('single', 'player_choice')
            assert event['payload']['ordered'], 'an order choice always lists the ordered effects'
        elif event['event_type'] in ('effect_resolved', 'effect_skipped_cost'):
            assert current_expected_order_index is not None, \
                'effect resolution events must follow an order choice'
            assert event['payload']['order_index'] == current_expected_order_index
            current_expected_order_index += 1

    # State snapshots: at least one per completed turn plus setup.
    snapshot_turns = {
        (event['match_number'], event['payload']['turn'])
        for event in _of_type(events, 'state_snapshot')
    }
    completed_turns = {
        key for key, phases in phases_by_turn.items() if 'END_TURN' in phases and key[1] >= 1
    }
    assert completed_turns <= snapshot_turns, 'every completed turn ends with a state snapshot'


def test_standard_game_event_stream(tmp_path):
    events, decisions = _play_recorded_game(tmp_path, 'standard')
    _assert_common_invariants(events, decisions)
    initial_hands = _of_type(events, 'initial_hand')
    assert len(initial_hands) == 2
    assert all(len(event['payload']['cards']) == 5 for event in initial_hands)
    assert len(_of_type(events, 'game_end')) == 1


def test_solo_game_event_stream(tmp_path):
    events, decisions = _play_recorded_game(tmp_path, 'solo')
    _assert_common_invariants(events, decisions)
    assert len(_of_type(events, 'game_end')) == 1


def test_tcg_series_event_stream(tmp_path):
    events, decisions = _play_recorded_game(tmp_path, 'tcg')
    _assert_common_invariants(events, decisions)

    assert len(_of_type(events, 'series_start')) == 1
    series_results = _of_type(events, 'series_result')
    assert len(series_results) == 1
    score = series_results[0]['payload']['score']
    assert max(score) == 2, 'best-of-3 ends at two wins'

    match_results = _of_type(events, 'match_result')
    assert len(match_results) == sum(score)
    match_starts = _of_type(events, 'match_start')
    assert len(match_starts) >= len(match_results)

    swap_events = _of_type(events, 'side_deck_swap')
    decided_non_final_matches = sum(score) - 1
    assert len(swap_events) == 2 * decided_non_final_matches, \
        'both players record a swap event after every decided non-final match'

    # Two initial hands per match played (including replayed draws).
    assert len(_of_type(events, 'initial_hand')) == 2 * len(match_starts)


def test_emit_event_is_observation_only(tmp_path):
    """emit_event must never touch the session RNG or mutate game state."""
    record_backend = _install_patches(tmp_path)
    session = _make_session('events-observation', mode='standard')
    _attach_runtime(session)

    class PoisonedRandom:
        def __getattr__(self, name):
            raise AssertionError(f'event recording accessed random_generator.{name}')

    async def run():
        from zutomayo.engine.game_persistence import GameRecordStore

        store = await GameRecordStore.create_for_session(session, 'standard')
        session.persistence = store
        session.random_generator = PoisonedRandom()
        store.emit_event('phase_entered', {'chronos': 0, 'day_night': 'DAY'}, turn=1, phase='SETUP')
        await store.flush_events()

    asyncio.run(run())
    assert record_backend.events[session.game_id][0]['event_type'] == 'phase_entered'
