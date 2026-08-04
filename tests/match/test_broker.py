"""MatchDecisionBroker: sequencing, timeout fallback, forfeit counting,
replay, divergence detection, and illegal-submission rejection."""

from __future__ import annotations

import asyncio

import pytest

from engine_alpha.actions import binary, select_card, select_number, P_EFFECT_TARGET, P_SKIP_SWAP, P_EFFECT_NUMBER
from zutomayo.match.broker import (
    CONSECUTIVE_TIMEOUT_FORFEIT_LIMIT,
    MatchDecisionBroker,
    MatchResumeDivergenceError,
    fallback_response_payload,
)
from zutomayo.match.decisions import (
    KIND_CARD_CHOICE,
    KIND_NUMBER_CHOICE,
    KIND_SIDE_CHOICE,
    PAYLOAD_ACTION,
    SIDE_ACTION_DAY,
    SIDE_ACTION_NIGHT,
    SIDE_LABEL_DAY,
    SIDE_LABEL_NIGHT,
    MatchDecisionOption,
    MatchDecisionRequest,
    request_fingerprint,
)
from tests.match.support import FakeSession, MemoryRecordStore


def make_request(engine_request, kind: str = KIND_CARD_CHOICE, player_index: int = 0,
                 timeout_seconds: float = 0.02) -> MatchDecisionRequest:
    return MatchDecisionRequest(
        kind=kind,
        player_index=player_index,
        prompt_text='test',
        engine_request=engine_request,
        purpose=engine_request.purpose,
        timeout_seconds=timeout_seconds,
    )


class ImmediateAdapter:
    def __init__(self, action: int) -> None:
        self.action = action
        self.broker = None

    async def present_decision(self, session, request) -> None:
        self.broker.submit(request.sequence_number, PAYLOAD_ACTION, self.action)


class SilentAdapter:
    async def present_decision(self, session, request) -> None:
        return None


class IllegalThenLegalAdapter:
    def __init__(self, illegal_action: int, legal_action: int) -> None:
        self.illegal_action = illegal_action
        self.legal_action = legal_action
        self.broker = None

    async def present_decision(self, session, request) -> None:
        self.broker.submit(request.sequence_number, PAYLOAD_ACTION, self.illegal_action)
        self.broker.submit(request.sequence_number, PAYLOAD_ACTION, self.legal_action)


def build_broker(adapter, persistence=None):
    session = FakeSession()
    broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, persistence)
    session.broker = broker
    if hasattr(adapter, 'broker'):
        adapter.broker = broker
    return broker


def test_sequence_numbers_and_persistence():
    adapter = ImmediateAdapter(action=1)
    store = MemoryRecordStore()
    broker = build_broker(adapter, store)

    async def run():
        for expected_sequence in range(3):
            request = make_request(select_card(P_EFFECT_TARGET, [10, 11, 12]))
            response = await broker.request(request)
            assert response.sequence_number == expected_sequence
            assert response.payload == 1
            assert response.timed_out is False

    asyncio.run(run())
    assert [record['sequence_number'] for record in store.decisions] == [0, 1, 2]
    assert all(record['payload_type'] == PAYLOAD_ACTION for record in store.decisions)


def test_timeout_fallback_pass_and_lowest_action():
    passable = select_card(P_EFFECT_TARGET, [10, 11], allow_pass=True)
    assert fallback_response_payload(make_request(passable)) == (PAYLOAD_ACTION, 2)

    mandatory = select_card(P_EFFECT_TARGET, [10, 11])
    assert fallback_response_payload(make_request(mandatory)) == (PAYLOAD_ACTION, 0)

    number = select_number(P_EFFECT_NUMBER, 3, 7)
    assert fallback_response_payload(make_request(number, kind=KIND_NUMBER_CHOICE)) == (PAYLOAD_ACTION, 3)

    skip_swap = binary(P_SKIP_SWAP)
    assert fallback_response_payload(make_request(skip_swap)) == (PAYLOAD_ACTION, 0)


def make_side_choice_request(player_index: int = 0, timeout_seconds: float = 0.02):
    return MatchDecisionRequest(
        kind=KIND_SIDE_CHOICE,
        player_index=player_index,
        prompt_text='pick a side',
        options=[
            MatchDecisionOption(SIDE_LABEL_DAY, 'opponent sets first', SIDE_ACTION_DAY),
            MatchDecisionOption(SIDE_LABEL_NIGHT, 'you set first', SIDE_ACTION_NIGHT),
        ],
        timeout_seconds=timeout_seconds,
    )


def test_side_choice_timeout_falls_back_to_day():
    request = make_side_choice_request()
    assert fallback_response_payload(request) == (PAYLOAD_ACTION, SIDE_ACTION_DAY)

    store = MemoryRecordStore()
    broker = build_broker(SilentAdapter(), store)

    async def run():
        response = await broker.request(make_side_choice_request(player_index=1))
        assert response.timed_out is True
        assert response.payload_type == PAYLOAD_ACTION
        assert response.payload == SIDE_ACTION_DAY

    asyncio.run(run())
    assert len(store.decisions) == 1
    record = store.decisions[0]
    assert record['timed_out'] is True
    assert record['fingerprint'] == {
        'kind': KIND_SIDE_CHOICE,
        'purpose': -1,
        'player_index': 1,
        'action_count': 2,
    }


def test_side_choice_answer_is_logged_with_its_label():
    store = MemoryRecordStore()
    adapter = ImmediateAdapter(action=SIDE_ACTION_NIGHT)
    broker = build_broker(adapter, store)

    async def run():
        response = await broker.request(make_side_choice_request(timeout_seconds=5.0))
        assert response.payload == SIDE_ACTION_NIGHT
        assert response.timed_out is False

    asyncio.run(run())
    described = store.events[-1]['payload']
    assert described['kind'] == KIND_SIDE_CHOICE
    assert described['chosen_label'] == SIDE_LABEL_NIGHT


def test_timeouts_count_toward_forfeit_and_reset():
    broker = build_broker(SilentAdapter())

    async def run():
        for _ in range(CONSECUTIVE_TIMEOUT_FORFEIT_LIMIT - 1):
            request = make_request(select_card(P_EFFECT_TARGET, [10, 11]))
            response = await broker.request(request)
            assert response.timed_out is True
        assert broker.timeout_forfeit_player() is None

        request = make_request(select_card(P_EFFECT_TARGET, [10, 11]))
        await broker.request(request)
        assert broker.timeout_forfeit_player() == 0

    asyncio.run(run())


def test_answer_resets_consecutive_timeouts():
    broker = build_broker(SilentAdapter())
    answered = ImmediateAdapter(action=0)
    answered.broker = broker

    async def run():
        await broker.request(make_request(select_card(P_EFFECT_TARGET, [10, 11])))
        assert broker.consecutive_timeouts[0] == 1
        broker.adapters[0] = answered
        await broker.request(make_request(select_card(P_EFFECT_TARGET, [10, 11])))
        assert broker.consecutive_timeouts[0] == 0

    asyncio.run(run())


def test_replay_answers_instantly_and_detects_divergence():
    adapter = ImmediateAdapter(action=1)
    store = MemoryRecordStore()
    broker = build_broker(adapter, store)

    async def record():
        await broker.request(make_request(select_card(P_EFFECT_TARGET, [10, 11, 12])))

    asyncio.run(record())

    replay_broker = build_broker(SilentAdapter())
    replay_broker.replay_log = store.replay_log()
    replay_broker.replaying = True

    async def replay_matching():
        response = await replay_broker.request(
            make_request(select_card(P_EFFECT_TARGET, [10, 11, 12])))
        assert response.payload == 1

    asyncio.run(replay_matching())

    diverged_broker = build_broker(SilentAdapter())
    diverged_broker.replay_log = store.replay_log()
    diverged_broker.replaying = True

    async def replay_diverged():
        with pytest.raises(MatchResumeDivergenceError):
            await diverged_broker.request(
                make_request(select_card(P_EFFECT_TARGET, [10, 11])))

    asyncio.run(replay_diverged())


def test_go_live_after_log_exhausted():
    adapter = ImmediateAdapter(action=0)
    broker = build_broker(adapter)
    broker.replaying = True
    went_live = []

    async def on_go_live():
        went_live.append(True)

    broker.on_go_live = on_go_live

    async def run():
        response = await broker.request(make_request(select_card(P_EFFECT_TARGET, [10, 11])))
        assert response.payload == 0

    asyncio.run(run())
    assert went_live == [True]
    assert broker.replaying is False


def test_illegal_submission_is_ignored():
    adapter = IllegalThenLegalAdapter(illegal_action=99, legal_action=1)
    broker = build_broker(adapter)

    async def run():
        response = await broker.request(
            make_request(select_card(P_EFFECT_TARGET, [10, 11, 12])))
        assert response.payload == 1

    asyncio.run(run())


def test_fingerprint_shape():
    request = make_request(select_card(P_EFFECT_TARGET, [10, 11], allow_pass=True))
    fingerprint = request_fingerprint(request)
    assert fingerprint == {
        'kind': KIND_CARD_CHOICE,
        'purpose': P_EFFECT_TARGET,
        'player_index': 0,
        'action_count': 3,
    }
