"""MatchDecisionBroker: sequencing, timeout fallback, forfeit counting,
replay, divergence detection, illegal-submission rejection, the accepted flag
submit returns, and is_pending. Also the decision descriptions persisted for the
summary view."""

from __future__ import annotations

import asyncio

import pytest

from engine_alpha.actions import (
    binary, select_card, select_identity, select_number,
    P_EFFECT_NUMBER, P_EFFECT_TARGET, P_NAME_GUESS, P_SKIP_SWAP,
)
from engine_alpha.cards import KEY_TO_INDEX, NUM_CARDS
from zutomayo.match import broker as broker_module
from zutomayo.match.broker import (
    CONSECUTIVE_TIMEOUT_FORFEIT_LIMIT,
    MatchDecisionBroker,
    MatchResumeDivergenceError,
    fallback_response_payload,
)
from zutomayo.match.decisions import (
    KIND_CARD_CHOICE,
    KIND_IDENTITY_INPUT,
    KIND_NUMBER_CHOICE,
    KIND_SIDE_CHOICE,
    PAYLOAD_ACTION,
    SIDE_ACTION_DAY,
    SIDE_ACTION_NIGHT,
    SIDE_LABEL_DAY,
    SIDE_LABEL_NIGHT,
    MatchDecisionOption,
    MatchDecisionRequest,
    MatchDecisionResponse,
    request_fingerprint,
)
from zutomayo.match.persistence import describe_match_decision
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


class SubmissionProbeAdapter:
    """Submits an unknown, an illegal, a legal and a repeated answer, and keeps what
    the broker reported for each."""

    def __init__(self) -> None:
        self.broker = None
        self.results: list[bool] = []

    async def present_decision(self, session, request) -> None:
        sequence_number = request.sequence_number
        self.results = [
            self.broker.submit(sequence_number + 1, PAYLOAD_ACTION, 0),
            self.broker.submit(sequence_number, PAYLOAD_ACTION, 99),
            self.broker.submit(sequence_number, PAYLOAD_ACTION, 1),
            self.broker.submit(sequence_number, PAYLOAD_ACTION, 0),
        ]


def test_submit_reports_whether_the_answer_was_accepted():
    adapter = SubmissionProbeAdapter()
    broker = build_broker(adapter)

    async def run():
        response = await broker.request(
            make_request(select_card(P_EFFECT_TARGET, [10, 11, 12]), timeout_seconds=5.0))
        assert response.payload == 1
        assert broker.submit(response.sequence_number, PAYLOAD_ACTION, 2) is False

    asyncio.run(run())
    assert adapter.results == [False, False, True, False]


def test_submit_after_a_timeout_is_refused():
    broker = build_broker(SilentAdapter())

    async def run():
        response = await broker.request(make_request(select_card(P_EFFECT_TARGET, [10, 11])))
        assert response.timed_out is True
        assert broker.submit(response.sequence_number, PAYLOAD_ACTION, 1) is False

    asyncio.run(run())


class PendingProbeAdapter:
    """Records is_pending while presenting; answers with `action`, or never if None."""

    def __init__(self, action: int | None = None) -> None:
        self.action = action
        self.broker = None
        self.while_presenting: list[bool] = []

    async def present_decision(self, session, request) -> None:
        sequence_number = request.sequence_number
        self.while_presenting.append(self.broker.is_pending(sequence_number))
        if self.action is not None:
            self.broker.submit(sequence_number, PAYLOAD_ACTION, self.action)
            self.while_presenting.append(self.broker.is_pending(sequence_number))


def test_is_pending_until_the_request_is_answered_or_times_out():
    answering, silent = PendingProbeAdapter(action=1), PendingProbeAdapter()
    broker = build_broker(answering)
    silent.broker = broker

    async def run():
        answered = await broker.request(
            make_request(select_card(P_EFFECT_TARGET, [10, 11]), timeout_seconds=5.0))
        broker.adapters[0] = silent
        timed_out = await broker.request(make_request(select_card(P_EFFECT_TARGET, [10, 11])))
        assert timed_out.timed_out is True
        assert broker.is_pending(answered.sequence_number) is False
        assert broker.is_pending(timed_out.sequence_number) is False
        assert broker.is_pending(timed_out.sequence_number + 1) is False, 'unknown request'

    asyncio.run(run())
    assert answering.while_presenting == [True, False], 'answered: no longer pending'
    assert silent.while_presenting == [True]


def make_hidden_hand_pick(hand_size: int = 4, timeout_seconds: float = 0.02) -> MatchDecisionRequest:
    request = make_request(select_number(P_EFFECT_NUMBER, 1, hand_size),
                           kind=KIND_NUMBER_CHOICE, timeout_seconds=timeout_seconds)
    request.hidden_hand_pick = True
    return request


def patch_system_random(monkeypatch) -> list:
    """Swap random.SystemRandom for a stand-in that picks the last option; returns
    what it is offered."""
    offered: list = []

    class LastChoiceRandom:
        def choice(self, sequence):
            offered.append(list(sequence))
            return sequence[-1]

    monkeypatch.setattr(broker_module.random, 'SystemRandom', LastChoiceRandom)
    return offered


def test_hidden_hand_pick_fallback_is_a_random_legal_position(monkeypatch):
    offered = patch_system_random(monkeypatch)
    assert fallback_response_payload(make_hidden_hand_pick()) == (PAYLOAD_ACTION, 4)
    assert offered == [[1, 2, 3, 4]], 'drawn from every legal position'


def test_hidden_hand_pick_fallback_is_legal_with_the_real_source():
    request = make_hidden_hand_pick()
    for _ in range(50):
        payload_type, position = fallback_response_payload(request)
        assert payload_type == PAYLOAD_ACTION
        assert request.engine_request.is_legal(position)


def test_hidden_hand_pick_timeout_logs_the_drawn_position(monkeypatch):
    patch_system_random(monkeypatch)
    store = MemoryRecordStore()
    broker = build_broker(SilentAdapter(), store)

    async def run():
        response = await broker.request(make_hidden_hand_pick(hand_size=3))
        assert response.timed_out is True
        assert response.payload == 3

    asyncio.run(run())
    assert store.decisions[0]['payload'] == 3, 'replay reads the logged position'


def test_other_fallbacks_never_draw_at_random(monkeypatch):
    class RefusingRandom:
        def __init__(self) -> None:
            raise AssertionError('only a hidden hand pick draws at random')

    monkeypatch.setattr(broker_module.random, 'SystemRandom', RefusingRandom)
    number = select_number(P_EFFECT_NUMBER, 1, 4)
    assert fallback_response_payload(make_request(number, kind=KIND_NUMBER_CHOICE)) == (PAYLOAD_ACTION, 1)
    identity = select_identity(P_NAME_GUESS, [5, 6, 7])
    assert fallback_response_payload(make_request(identity, kind=KIND_IDENTITY_INPUT)) == (PAYLOAD_ACTION, 5)
    passable = select_card(P_EFFECT_TARGET, [10, 11], allow_pass=True)
    assert fallback_response_payload(make_request(passable)) == (PAYLOAD_ACTION, 2)


# -- persisted decision descriptions -------------------------------------------


def test_name_guess_is_described_by_the_card_name():
    request = make_request(select_identity(P_NAME_GUESS, list(range(NUM_CARDS))),
                           kind=KIND_IDENTITY_INPUT)
    named = KEY_TO_INDEX['02-079']
    described = describe_match_decision(request, MatchDecisionResponse(0, PAYLOAD_ACTION, named))
    assert described['action'] == named
    assert described['chosen_label'] == 'NIRA-CHAN (Humanoid) [にらちゃん（ヒューマノイド）]'


def test_hidden_hand_pick_is_described_without_its_position():
    described = describe_match_decision(
        make_hidden_hand_pick(), MatchDecisionResponse(0, PAYLOAD_ACTION, 3))
    assert described['chosen_label'] == 'Face-down card'
    assert 'chosen_description' not in described

    plain = make_request(select_number(P_EFFECT_NUMBER, 0, 5), kind=KIND_NUMBER_CHOICE)
    described = describe_match_decision(plain, MatchDecisionResponse(0, PAYLOAD_ACTION, 3))
    assert 'chosen_label' not in described, 'other number picks show the bare number'
