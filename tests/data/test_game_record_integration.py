"""
PostgreSQL integration tests for game records, decisions, and game ids.

Skipped unless ZUTOKA_TEST_DATABASE_URL is set; see docs/postgresql_setup.md.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from engine_alpha.actions import select_number, P_EFFECT_NUMBER
from tests.support.database_support import run_with_database
from zutomayo.data.game_id_allocator import PostgresGameIdAllocator
from zutomayo.engine.game_persistence import (
    PostgresGameRecordBackend,
    list_game_ids_with_status,
    load_manifest,
)
from zutomayo.engine.game_session import GameSession
from zutomayo.match.decisions import (
    KIND_NUMBER_CHOICE,
    PAYLOAD_ACTION,
    MatchDecisionRequest,
    MatchDecisionResponse,
)
from zutomayo.match.persistence import MatchRecordStore, load_match_decision_log


def _use_postgres_backends(monkeypatch):
    import zutomayo.data.game_id_allocator as game_id_allocator_module
    import zutomayo.engine.game_persistence as game_persistence_module

    monkeypatch.setattr(game_persistence_module, 'backend', PostgresGameRecordBackend())
    monkeypatch.setattr(game_id_allocator_module, 'backend', PostgresGameIdAllocator())


def _make_session() -> GameSession:
    session = GameSession(game_id='20260710-00000', channel_id=42, creator_id=111)
    session.add_player(222)
    # Exercise the NUMERIC(20,0) path: a 64-bit seed above the signed BIGINT range.
    session.random_seed = 2 ** 63 + 12345
    session.player_deck_names = {0: 'My Deck', 1: None}
    return session


def test_game_record_round_trip_with_large_seed(integration_database_url, monkeypatch):
    _use_postgres_backends(monkeypatch)
    session = _make_session()

    async def round_trip():
        store = await MatchRecordStore.create_for_match(
            session, 'standard',
            engine_seed=session.random_seed,
            deck_card_keys={0: [[1, 5]], 1: [[2, 3]]},
        )

        request = MatchDecisionRequest(
            kind=KIND_NUMBER_CHOICE, player_index=0, prompt_text='number',
            engine_request=select_number(P_EFFECT_NUMBER, 0, 3),
            purpose=P_EFFECT_NUMBER,
            minimum_value=0, maximum_value=3,
        )
        request.sequence_number = 0
        await store.append_decision(request, MatchDecisionResponse(0, PAYLOAD_ACTION, 2))

        manifest = await load_manifest(session.game_id)
        replay_log = await load_match_decision_log(session.game_id)
        active_ids = await list_game_ids_with_status('active')

        await store.set_status(
            'completed', winner_index=0, result_summary={'result': 'PLAYER_1_WIN', 'turns': 4},
        )
        from zutomayo.engine.game_persistence import backend
        game_row = await backend.get_game_row(session.game_id)
        return manifest, replay_log, active_ids, game_row

    manifest, replay_log, active_ids, game_row = run_with_database(
        integration_database_url, round_trip,
    )
    assert manifest['random_seed'] == 2 ** 63 + 12345, 'JSONB preserves the full seed'
    assert int(game_row['random_seed']) == 2 ** 63 + 12345, 'NUMERIC preserves the full seed'
    assert manifest['player_discord_ids'] == [[111, 0], [222, 1]]
    assert replay_log[0][1].payload == 2
    assert active_ids == ['20260710-00000']
    assert game_row['status'] == 'completed'
    assert game_row['winner_index'] == 0
    assert game_row['result_summary'] == {'result': 'PLAYER_1_WIN', 'turns': 4}
    assert game_row['ended_at'] is not None


def test_game_id_allocation_against_postgres(integration_database_url, monkeypatch):
    _use_postgres_backends(monkeypatch)
    from zutomayo.data.game_id_allocator import allocate_game_id

    async def allocate():
        moment = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        return [await allocate_game_id(moment) for _ in range(3)]

    assert run_with_database(integration_database_url, allocate) == [
        '20260710-00000', '20260710-00001', '20260710-00002',
    ]
