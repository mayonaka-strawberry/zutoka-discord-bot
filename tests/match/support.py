"""Shared fakes for match-layer tests: a headless session, a recording
transport, a scripted decision adapter, and an in-memory record store."""

from __future__ import annotations

import random
from typing import Any, Optional

from zutomayo.match.decisions import (
    PAYLOAD_ACTION,
    PAYLOAD_CARD_KEYS,
    MatchDecisionRequest,
)
from zutomayo.match.persistence import describe_match_decision
from zutomayo.match.decisions import request_fingerprint


class FakeSession:
    def __init__(self, game_id: str = 'TEST-00000') -> None:
        self.game_id = game_id
        self.channel_id = 123
        self.player_discord_ids = {111: 0, 222: 1}
        self.player_deck_names: dict[int, Optional[str]] = {0: None, 1: None}
        self.is_solo = False
        self.solo_difficulty = 'normal'
        self.is_tcg = False
        self.best_of = 0
        self.broker: Any = None
        self.transport: Any = None
        self.persistence: Any = None
        self.game: Any = None


class RecordingTransport:
    def __init__(self) -> None:
        self.muted = False
        self.suppress_phase_delays = True
        self.player_messages: dict[int, list[dict]] = {0: [], 1: []}
        self.channel_messages: list[dict] = []

    async def send_to_player(self, session, player_index: int, **kwargs) -> None:
        if not self.muted:
            self.player_messages[player_index].append(kwargs)
        return None

    async def send_to_channel(self, session, **kwargs) -> None:
        if not self.muted:
            self.channel_messages.append(kwargs)
        return None

    def display_name(self, session, player_index: int) -> str:
        return f'Player {player_index + 1}'

    def delivers_to_player(self, session, player_index: int) -> bool:
        return not self.muted


class ScriptedActionAdapter:
    """Answers every request with a legal action that is a pure function of
    (seed, sequence_number, action_count) - stateless, so a replay truncated
    at any point produces identical live answers for the remainder.

    Bot-layer requests (no engine request) answer from their options with the
    same mixing; the TCG side-deck switch, which carries no options, answers
    with an empty swap."""

    def __init__(self, broker_getter, seed: int = 0) -> None:
        self.broker_getter = broker_getter
        self.seed = seed

    def _choice_index(self, request: MatchDecisionRequest, count: int) -> int:
        mixed = (self.seed * 1000003 + request.sequence_number * 2654435761 + count) & 0xFFFFFFFF
        return mixed % count

    async def present_decision(self, session, request: MatchDecisionRequest) -> None:
        engine_request = request.engine_request
        if engine_request is None:
            if request.options:
                actions = [option.action for option in request.options]
                self.broker_getter().submit(
                    request.sequence_number, PAYLOAD_ACTION,
                    actions[self._choice_index(request, len(actions))],
                )
                return
            self.broker_getter().submit(
                request.sequence_number, PAYLOAD_CARD_KEYS, {'removed': [], 'added': []},
            )
            return
        legal = engine_request.legal_actions()
        action = legal[self._choice_index(request, len(legal))]
        self.broker_getter().submit(request.sequence_number, PAYLOAD_ACTION, action)


class MemoryRecordStore:
    """In-memory MatchRecordStore stand-in: same append/emit surface."""

    def __init__(self, game_id: str = 'TEST-00000', session: Any = None) -> None:
        self.game_id = game_id
        self.session = session
        self.decisions: list[dict] = []
        self.events: list[dict] = []
        self.status_history: list[dict] = []

    def _replaying(self) -> bool:
        return (
            self.session is not None
            and self.session.broker is not None
            and self.session.broker.replaying
        )

    def emit_event(self, event_type: str, payload: dict, **context) -> None:
        if self._replaying():
            return
        self.events.append({'event_type': event_type, 'payload': payload, **context})

    async def flush_events(self) -> None:
        return None

    async def append_decision(self, request, response) -> None:
        self.decisions.append({
            'sequence_number': response.sequence_number,
            'fingerprint': request_fingerprint(request),
            'payload_type': response.payload_type,
            'payload': response.payload,
            'timed_out': response.timed_out,
        })
        self.emit_event('decision_made', describe_match_decision(request, response))

    async def set_status(self, status: str, **kwargs) -> None:
        self.status_history.append({'status': status, **kwargs})

    def replay_log(self):
        from zutomayo.match.decisions import MatchDecisionResponse

        log = {}
        for record in self.decisions:
            response = MatchDecisionResponse(
                sequence_number=record['sequence_number'],
                payload_type=record['payload_type'],
                payload=record['payload'],
                timed_out=record['timed_out'],
            )
            log[record['sequence_number']] = (record['fingerprint'], response)
        return log


def random_full_pool_decks(seed: int) -> tuple[list[int], list[int]]:
    from engine_alpha import cards

    rng = random.Random(seed)
    all_definitions = [d.index for d in cards.CARD_DB]

    def one_deck() -> list[int]:
        deck = []
        for definition_index in rng.sample(all_definitions, 10):
            deck.extend((definition_index, definition_index))
        return deck

    return one_deck(), one_deck()


def state_digest(game) -> tuple:
    state = game.state
    players = tuple(
        (p.hp, tuple(p.deck), tuple(p.hand), tuple(p.charger), tuple(p.abyss),
         p.battle, p.set_a, p.set_b, p.set_c)
        for p in state.players
    )
    return (state.phase, state.turn, state.chronos, state.winner,
            state.last_battle_winner, players, state.rng_ctr)
