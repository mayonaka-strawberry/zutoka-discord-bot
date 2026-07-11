"""
Harness for exercising a single card effect through the real EffectEngine.

Each run builds a real GameSession around a prepared GameState, binds a real
EffectEngine (broker + recording transport, seeded RNG), and dispatches one
effect exactly the way process_effects would. Prompts are answered from a
scripted list; running out of answers or receiving an unexpected kind fails
the test loudly.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from zutomayo.effects.effect_engine import EffectEngine, _EFFECT_HANDLERS
from zutomayo.engine.decision_broker import DecisionBroker
from zutomayo.engine.decisions import (
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TEXT,
    DecisionRequest,
)
from zutomayo.engine.game_session import GameSession
from zutomayo.engine.turn_manager import TurnManager
from zutomayo.models.game_state import GameState

from tests.fake_adapters import RecordingTransport
from tests.transcript import TranscriptRecorder

_TIMEOUT_SENTINEL = object()

HARNESS_SEED = 1234


@dataclass
class ScriptedAnswer:
    """One scripted prompt answer. kind is asserted against the request."""
    kind: str
    payload_type: str
    payload: Any

    @classmethod
    def card_indices(cls, indices: list[int], kind: str = 'effect_card_select') -> 'ScriptedAnswer':
        return cls(kind, PAYLOAD_INDICES, indices)

    @classmethod
    def number(cls, value: int) -> 'ScriptedAnswer':
        return cls('effect_number_select', PAYLOAD_NUMBER, value)

    @classmethod
    def text(cls, value: Optional[str]) -> 'ScriptedAnswer':
        return cls('effect_text_input', PAYLOAD_TEXT, value)

    @classmethod
    def timeout(cls, kind: str) -> 'ScriptedAnswer':
        return cls(kind, 'timeout', _TIMEOUT_SENTINEL)


class QueuedAnswerAdapter:
    """Answers broker prompts from a scripted queue; records what was asked."""

    def __init__(self, recorder: TranscriptRecorder) -> None:
        self.answers: deque[ScriptedAnswer] = deque()
        self.recorder = recorder
        self.prompts_seen: list[str] = []

    async def present_decision(self, session: 'GameSession', request: DecisionRequest) -> None:
        self.prompts_seen.append(request.kind)
        if not self.answers:
            raise AssertionError(
                f'Unexpected prompt {request.kind!r} for player {request.player_index} '
                f'(no scripted answers left): {request.prompt_text[:120]!r}'
            )
        answer = self.answers.popleft()
        assert answer.kind == request.kind, (
            f'Prompt kind mismatch: scripted {answer.kind!r}, engine asked {request.kind!r} '
            f'({request.prompt_text[:120]!r})'
        )
        self.recorder.record_prompt(
            request.kind, request.player_index,
            [option.label for option in request.options],
            answer.payload_type,
            None if answer.payload is _TIMEOUT_SENTINEL else answer.payload,
        )
        if answer.payload is _TIMEOUT_SENTINEL:
            # Let the broker's own timeout fire quickly instead of answering.
            request.timeout_seconds = 0.01
            return
        session.broker.submit(request.sequence_number, answer.payload_type, answer.payload)


@dataclass
class EffectRunResult:
    state: GameState
    engine: EffectEngine
    turn_manager: TurnManager
    session: GameSession
    recorder: TranscriptRecorder
    prompts_seen: list[str] = field(default_factory=list)

    @property
    def messages(self) -> list[dict]:
        return [event for event in self.recorder.events if event['event'] == 'send']

    def message_texts(self) -> list[str]:
        return [event['content'] for event in self.messages if event.get('content')]


class EffectHarness:
    """Builds the runtime around a state and dispatches effects through it."""

    def __init__(self, state: GameState) -> None:
        self.state = state
        self.recorder = TranscriptRecorder()
        self.session = GameSession(game_id='effect-test', channel_id=1, creator_id=111111)
        self.session.add_player(222222)
        self.session.random_seed = HARNESS_SEED
        self.session.random_generator = random.Random(HARNESS_SEED)
        self.session.game_state = state

        self.engine = EffectEngine()
        self.session.effect_engine = self.engine
        self.turn_manager = TurnManager(state, self.engine)
        self.session.turn_manager = self.turn_manager

        self.adapter = QueuedAnswerAdapter(self.recorder)
        self.session.transport = RecordingTransport(self.recorder)
        self.session.broker = DecisionBroker(self.session, {0: self.adapter, 1: self.adapter})
        self.engine.bind(self.session, None)

    def run_effect(
        self,
        effect_id: str,
        owner_index: int,
        scripted_answers: Optional[list[ScriptedAnswer]] = None,
        card_instance: Any = None,
    ) -> EffectRunResult:
        """
        Dispatch one effect the way process_effects does.

        card_instance defaults to the owner's battle-zone card; pass another
        CardInstance explicitly for set-zone or enchant effects.
        """
        assert effect_id in _EFFECT_HANDLERS, f'{effect_id} has no registered handler'
        if card_instance is None:
            card_instance = self.state.players[owner_index].battle_zone
            assert card_instance is not None, 'no card_instance given and battle zone empty'
        assert card_instance.card.effect == effect_id, (
            f'card_instance is {card_instance.card.effect}, expected {effect_id}'
        )
        self.adapter.answers.extend(scripted_answers or [])

        asyncio.run(self.engine._dispatch(self.state, owner_index, card_instance))

        assert not self.adapter.answers, (
            f'{len(self.adapter.answers)} scripted answer(s) were never consumed'
        )
        return EffectRunResult(
            state=self.state,
            engine=self.engine,
            turn_manager=self.turn_manager,
            session=self.session,
            recorder=self.recorder,
            prompts_seen=self.adapter.prompts_seen,
        )


def run_effect(
    state: GameState,
    effect_id: str,
    owner_index: int,
    scripted_answers: Optional[list[ScriptedAnswer]] = None,
    card_instance: Any = None,
) -> EffectRunResult:
    """One-shot convenience wrapper around EffectHarness."""
    harness = EffectHarness(state)
    return harness.run_effect(effect_id, owner_index, scripted_answers, card_instance)


def card_identities(instances: list[Any]) -> list[str]:
    """Pack-id strings for a zone's contents, for compact assertions."""
    result = []
    for instance in instances:
        card = getattr(instance, 'card', instance)
        result.append(f'{card.pack:02d}-{card.id:03d}')
    return result
