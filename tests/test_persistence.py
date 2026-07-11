"""Unit tests for the game record store: manifest round-trip, decision log
append/load, status transitions, and idempotent inserts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.engine.decisions import (  # noqa: E402
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TIMEOUT,
    DecisionOption,
    DecisionRequest,
    DecisionResponse,
    request_fingerprint,
)
from zutomayo.engine.game_persistence import (  # noqa: E402
    GameRecordStore,
    list_game_ids_with_status,
    load_decision_log,
    load_manifest,
)
from zutomayo.engine.game_session import GameSession  # noqa: E402


def _make_session() -> GameSession:
    session = GameSession(game_id='20260710-00000', channel_id=42, creator_id=111)
    session.add_player(222)
    session.random_seed = 987654321
    return session


def test_manifest_round_trip(install_in_memory_backends):
    session = _make_session()
    session.player_deck_names = {0: 'My Deck', 1: None}

    async def create_and_load():
        await GameRecordStore.create_for_session(session, 'standard', extra_fields={
            'deck_0': [[1, 5], [2, 17]],
            'deck_1': [[3, 8], [4, 2]],
        })
        return await load_manifest('20260710-00000')

    manifest = asyncio.run(create_and_load())
    assert manifest['game_id'] == '20260710-00000'
    assert manifest['channel_id'] == 42
    assert manifest['mode'] == 'standard'
    assert manifest['player_discord_ids'] == [[111, 0], [222, 1]]
    assert manifest['player_deck_names'] == {'0': 'My Deck', '1': None}
    assert manifest['random_seed'] == 987654321
    assert manifest['deck_0'] == [[1, 5], [2, 17]]
    assert manifest['deck_1'] == [[3, 8], [4, 2]]

    game_row = install_in_memory_backends['game_records'].games['20260710-00000']
    assert game_row['status'] == 'active'
    assert game_row['mode'] == 'standard'


def test_decision_log_append_and_load():
    session = _make_session()

    request_a = DecisionRequest(
        kind=KIND_EFFECT_CARD_SELECT, player_index=0, prompt_text='pick',
        options=[DecisionOption('01-001', 'A', 0), DecisionOption('01-002', 'B', 1)],
    )
    request_a.sequence_number = 0
    request_b = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=1, prompt_text='number',
        minimum_value=0, maximum_value=5,
    )
    request_b.sequence_number = 1

    async def append_and_load():
        store = await GameRecordStore.create_for_session(session, 'standard')
        await store.append_decision(request_a, DecisionResponse(0, PAYLOAD_INDICES, [1]))
        await store.append_decision(request_b, DecisionResponse(1, PAYLOAD_TIMEOUT, None))
        return await load_decision_log(session.game_id)

    replay_log = asyncio.run(append_and_load())
    assert set(replay_log.keys()) == {0, 1}
    fingerprint_a, response_a = replay_log[0]
    assert fingerprint_a == request_fingerprint(request_a)
    assert response_a.payload_type == PAYLOAD_INDICES
    assert response_a.payload == [1]
    fingerprint_b, response_b = replay_log[1]
    assert response_b.payload_type == PAYLOAD_TIMEOUT
    assert response_b.payload is None


def test_duplicate_decision_inserts_are_ignored():
    session = _make_session()
    request = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
        minimum_value=0, maximum_value=3,
    )
    request.sequence_number = 0

    async def append_twice():
        store = await GameRecordStore.create_for_session(session, 'standard')
        await store.append_decision(request, DecisionResponse(0, PAYLOAD_NUMBER, 2))
        await store.append_decision(request, DecisionResponse(0, PAYLOAD_NUMBER, 3))
        return await load_decision_log(session.game_id)

    replay_log = asyncio.run(append_twice())
    assert replay_log[0][1].payload == 2, 'the first write wins'


def test_status_transitions_and_listing(install_in_memory_backends):
    session = _make_session()

    async def transition():
        store = await GameRecordStore.create_for_session(session, 'standard')
        active_ids = await list_game_ids_with_status('active')
        await store.set_status('saved')
        saved_ids = await list_game_ids_with_status('saved')
        await store.set_status(
            'completed', winner_index=1,
            result_summary={'result': 'PLAYER_2_WIN', 'turns': 9},
        )
        return active_ids, saved_ids

    active_ids, saved_ids = asyncio.run(transition())
    assert active_ids == ['20260710-00000']
    assert saved_ids == ['20260710-00000']

    game_row = install_in_memory_backends['game_records'].games['20260710-00000']
    assert game_row['status'] == 'completed'
    assert game_row['winner_index'] == 1
    assert game_row['result_summary'] == {'result': 'PLAYER_2_WIN', 'turns': 9}
    assert game_row['saved_at'] is not None
    assert game_row['ended_at'] is not None


def test_attach_for_resume_appends_to_the_same_log():
    session = _make_session()
    request = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
        minimum_value=0, maximum_value=3,
    )
    request.sequence_number = 0
    later_request = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=1, prompt_text='number',
        minimum_value=0, maximum_value=3,
    )
    later_request.sequence_number = 1

    async def append_across_attach():
        store = await GameRecordStore.create_for_session(session, 'standard')
        await store.append_decision(request, DecisionResponse(0, PAYLOAD_NUMBER, 2))
        reattached = GameRecordStore.attach_for_resume(session.game_id)
        await reattached.append_decision(later_request, DecisionResponse(1, PAYLOAD_NUMBER, 1))
        return await load_decision_log(session.game_id)

    replay_log = asyncio.run(append_across_attach())
    assert set(replay_log.keys()) == {0, 1}
