"""
Smoke tests for the DecisionBroker: request/submit roundtrips for every
decision kind, timeout handling, late submissions, deterministic sequence
numbering, and replay behavior. Runs under pytest or directly:
    python tests/test_broker_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.engine.decision_broker import DecisionBroker, ResumeDivergenceError  # noqa: E402
from zutomayo.engine.decisions import (  # noqa: E402
    KIND_CARD_SELECT,
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    KIND_EFFECT_TEXT_INPUT,
    KIND_REDRAW,
    KIND_TCG_SWITCH,
    KIND_TWO_STEP_CARD_SELECT,
    PAYLOAD_CARD_KEYS,
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TEXT,
    PAYLOAD_TIMEOUT,
    DecisionOption,
    DecisionRequest,
    DecisionResponse,
    request_fingerprint,
)


class ScriptedInlineAdapter:
    """Answers every request immediately from a scripted (payload_type, payload) list."""

    def __init__(self, answers: list[tuple[str, object]]) -> None:
        self.answers = list(answers)
        self.presented: list[DecisionRequest] = []

    async def present_decision(self, session, request: DecisionRequest) -> None:
        self.presented.append(request)
        payload_type, payload = self.answers.pop(0)
        session.broker.submit(request.sequence_number, payload_type, payload)


class SilentAdapter:
    """Never answers; used to exercise timeouts."""

    async def present_decision(self, session, request: DecisionRequest) -> None:
        pass


def make_session_and_broker(adapter) -> tuple[SimpleNamespace, DecisionBroker]:
    session = SimpleNamespace(game_id='smoke-test', broker=None)
    broker = DecisionBroker(session, adapters={0: adapter, 1: adapter})
    session.broker = broker
    return session, broker


def three_options() -> list[DecisionOption]:
    return [
        DecisionOption(label='01-001', description='Card A', value_index=0),
        DecisionOption(label='01-002', description='Card B', value_index=1),
        DecisionOption(label='01-003', description='Card C', value_index=2),
    ]


def test_every_kind_roundtrips() -> None:
    answers = [
        (PAYLOAD_INDICES, [1]),                   # effect_card_select
        (PAYLOAD_NUMBER, 3),                      # effect_number_select
        (PAYLOAD_TEXT, '02-005'),                 # effect_text_input
        (PAYLOAD_INDICES, []),                    # redraw (keep hand)
        (PAYLOAD_INDICES, [0]),                   # card_select
        (PAYLOAD_INDICES, [2, 0]),                # two_step_card_select
        (PAYLOAD_CARD_KEYS, {'removed': [[1, 5]], 'added': [[2, 7]]}),  # tcg_switch
    ]
    adapter = ScriptedInlineAdapter(answers)
    session, broker = make_session_and_broker(adapter)

    async def run() -> list[DecisionResponse]:
        requests = [
            DecisionRequest(kind=KIND_EFFECT_CARD_SELECT, player_index=0, prompt_text='pick', options=three_options()),
            DecisionRequest(kind=KIND_EFFECT_NUMBER_SELECT, player_index=1, prompt_text='number', minimum_value=0, maximum_value=5),
            DecisionRequest(kind=KIND_EFFECT_TEXT_INPUT, player_index=0, prompt_text='type'),
            DecisionRequest(kind=KIND_REDRAW, player_index=1, prompt_text='redraw', options=three_options()),
            DecisionRequest(kind=KIND_CARD_SELECT, player_index=0, prompt_text='set', options=three_options()),
            DecisionRequest(kind=KIND_TWO_STEP_CARD_SELECT, player_index=1, prompt_text='set two', options=three_options()),
            DecisionRequest(kind=KIND_TCG_SWITCH, player_index=0, prompt_text='switch'),
        ]
        return [await broker.request(request) for request in requests]

    responses = asyncio.run(run())
    assert [response.sequence_number for response in responses] == [0, 1, 2, 3, 4, 5, 6]
    assert responses[0].payload == [1]
    assert responses[1].payload == 3
    assert responses[2].payload == '02-005'
    assert responses[3].payload == []
    assert responses[4].payload == [0]
    assert responses[5].payload == [2, 0]
    assert responses[6].payload == {'removed': [[1, 5]], 'added': [[2, 7]]}
    assert len(adapter.presented) == 7


def test_timeout_becomes_logged_decision_and_late_submit_is_ignored() -> None:
    session, broker = make_session_and_broker(SilentAdapter())

    async def run() -> DecisionResponse:
        request = DecisionRequest(
            kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
            minimum_value=1, maximum_value=2, timeout_seconds=0.05,
        )
        response = await broker.request(request)
        # A button pressed after the timeout must be a harmless no-op.
        broker.submit(request.sequence_number, PAYLOAD_NUMBER, 2)
        return response

    response = asyncio.run(run())
    assert response.payload_type == PAYLOAD_TIMEOUT
    assert response.payload is None


def test_concurrent_requests_number_in_code_order() -> None:
    adapter = ScriptedInlineAdapter([(PAYLOAD_INDICES, [0]), (PAYLOAD_INDICES, [1])])
    session, broker = make_session_and_broker(adapter)

    async def run() -> tuple[DecisionResponse, DecisionResponse]:
        request_for_player_0 = DecisionRequest(kind=KIND_CARD_SELECT, player_index=0, prompt_text='p0', options=three_options())
        request_for_player_1 = DecisionRequest(kind=KIND_CARD_SELECT, player_index=1, prompt_text='p1', options=three_options())
        return await asyncio.gather(
            broker.request(request_for_player_0),
            broker.request(request_for_player_1),
        )

    response_0, response_1 = asyncio.run(run())
    assert response_0.sequence_number == 0
    assert response_1.sequence_number == 1


def test_replay_returns_logged_answers_then_goes_live() -> None:
    adapter = ScriptedInlineAdapter([(PAYLOAD_NUMBER, 4)])
    session, broker = make_session_and_broker(adapter)

    live_request_template = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
        minimum_value=0, maximum_value=5,
    )
    logged_response = DecisionResponse(0, PAYLOAD_NUMBER, 2)
    broker.replay_log = {0: (request_fingerprint(live_request_template), logged_response)}
    broker.replaying = True
    went_live = []

    async def on_go_live() -> None:
        went_live.append(True)

    broker.on_go_live = on_go_live

    async def run() -> tuple[DecisionResponse, DecisionResponse]:
        first = await broker.request(DecisionRequest(
            kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
            minimum_value=0, maximum_value=5,
        ))
        second = await broker.request(DecisionRequest(
            kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
            minimum_value=0, maximum_value=5,
        ))
        return first, second

    first, second = asyncio.run(run())
    assert first.payload == 2, 'first answer must come from the replay log'
    assert len(adapter.presented) == 1, 'adapter must not be consulted during replay'
    assert went_live == [True]
    assert second.payload == 4, 'second answer must come from the live adapter'
    assert broker.replaying is False


def test_replay_divergence_raises() -> None:
    session, broker = make_session_and_broker(SilentAdapter())
    logged_request = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
        minimum_value=0, maximum_value=5,
    )
    broker.replay_log = {0: (request_fingerprint(logged_request), DecisionResponse(0, PAYLOAD_NUMBER, 2))}
    broker.replaying = True

    async def run() -> None:
        diverged_request = DecisionRequest(
            kind=KIND_EFFECT_CARD_SELECT, player_index=0, prompt_text='pick',
            options=three_options(),
        )
        await broker.request(diverged_request)

    try:
        asyncio.run(run())
    except ResumeDivergenceError:
        pass
    else:
        raise AssertionError('expected ResumeDivergenceError')


if __name__ == '__main__':
    test_every_kind_roundtrips()
    test_timeout_becomes_logged_decision_and_late_submit_is_ignored()
    test_concurrent_requests_number_in_code_order()
    test_replay_returns_logged_answers_then_goes_live()
    test_replay_divergence_raises()
    print('broker smoke tests passed')
