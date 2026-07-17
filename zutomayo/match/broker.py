"""
MatchDecisionBroker: the single choke point for every interactive decision in
an engine_alpha-driven match.

The driver calls ``await broker.request(match_request)`` and gets back a
MatchDecisionResponse. The broker:

- assigns deterministic sequence numbers (incremented at request() entry, in
  code order, so numbering is stable even when both players are prompted
  concurrently),
- during replay (after a bot restart or /resume) answers requests instantly
  from the loaded decision log, verifying each request's fingerprint,
- live, presents the decision through the requesting player's adapter
  (Discord views, a model agent, or a scripted test adapter) and awaits the
  answer with the request's timeout,
- resolves timeouts to a deterministic fallback action (PASS when the
  decision allows passing, otherwise the lowest legal action), logs it as a
  normal action flagged ``timed_out``, and tracks consecutive timeouts per
  player so the driver can forfeit a player who stopped answering,
- appends every response to the game's persistence log when one is attached.

This module must stay import-light: no discord, no views, no engine imports.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Protocol

from zutomayo.match.decisions import (
    KIND_SIDE_DECK_SWITCH,
    PAYLOAD_ACTION,
    PAYLOAD_CARD_KEYS,
    MatchDecisionRequest,
    MatchDecisionResponse,
    request_fingerprint,
)

log = logging.getLogger(__name__)

CONSECUTIVE_TIMEOUT_FORFEIT_LIMIT = 3


class MatchDecisionAdapter(Protocol):
    """Presents a decision to whoever answers for one player."""

    async def present_decision(self, session: Any, request: MatchDecisionRequest) -> None:
        ...


class MatchResumeDivergenceError(Exception):
    """A replayed request no longer matches the logged decision sequence."""

    def __init__(self, request: MatchDecisionRequest, logged_fingerprint: Optional[dict],
                 live_fingerprint: dict) -> None:
        super().__init__(
            f'Replay diverged at sequence {request.sequence_number}: '
            f'logged {logged_fingerprint!r} vs live {live_fingerprint!r}'
        )
        self.request = request


def fallback_response_payload(request: MatchDecisionRequest) -> tuple[str, Any]:
    """Deterministic payload applied when a player times out: PASS when the
    engine decision allows passing, otherwise the lowest legal action; an
    empty switch for the TCG side-deck decision."""
    engine_request = request.engine_request
    if engine_request is None:
        if request.kind == KIND_SIDE_DECK_SWITCH:
            return PAYLOAD_CARD_KEYS, {'removed': [], 'added': []}
        raise ValueError(f'no fallback for bot-layer kind {request.kind!r}')
    if engine_request.allow_pass:
        return PAYLOAD_ACTION, len(engine_request.candidates)
    return PAYLOAD_ACTION, engine_request.legal_actions()[0]


class MatchDecisionBroker:
    def __init__(
        self,
        session: Any,
        adapters: dict[int, MatchDecisionAdapter],
        persistence: Any = None,
    ) -> None:
        self.session = session
        self.adapters = adapters
        self.persistence = persistence
        self.issue_counter = 0
        # Loaded on resume: sequence_number -> (fingerprint dict, MatchDecisionResponse)
        self.replay_log: dict[int, tuple[dict, MatchDecisionResponse]] = {}
        self.replaying = False
        self.pending_futures: dict[int, tuple[MatchDecisionRequest, asyncio.Future]] = {}
        self.consecutive_timeouts: dict[int, int] = {0: 0, 1: 0}
        # Called once when the replay log is exhausted and the game goes live
        # (unmute transport, announce the resume). Installed by the resume
        # manager; a no-op for games that never restarted.
        self.on_go_live: Any = None

    async def request(self, request: MatchDecisionRequest) -> MatchDecisionResponse:
        request.sequence_number = self.issue_counter
        self.issue_counter += 1

        if self.replaying:
            logged = self.replay_log.get(request.sequence_number)
            if logged is not None:
                logged_fingerprint, logged_response = logged
                live_fingerprint = request_fingerprint(request)
                if logged_fingerprint != live_fingerprint:
                    raise MatchResumeDivergenceError(request, logged_fingerprint, live_fingerprint)
                return logged_response
            await self._go_live()

        adapter = self.adapters[request.player_index]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending_futures[request.sequence_number] = (request, future)
        timed_out = False
        try:
            await adapter.present_decision(self.session, request)
            try:
                payload_type, payload = await asyncio.wait_for(future, request.timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                payload_type, payload = fallback_response_payload(request)
        finally:
            self.pending_futures.pop(request.sequence_number, None)

        if timed_out:
            self.consecutive_timeouts[request.player_index] += 1
        else:
            self.consecutive_timeouts[request.player_index] = 0

        response = MatchDecisionResponse(
            request.sequence_number, payload_type, payload, timed_out=timed_out,
        )
        if self.persistence is not None:
            await self.persistence.append_decision(request, response)
        return response

    def submit(self, sequence_number: int, payload_type: str, payload: Any) -> None:
        """
        Deliver a player's answer. Called by Discord views and model agent
        adapters. Answers for unknown or already-resolved sequence numbers
        (for example a button pressed after the prompt timed out) are
        ignored, as are actions the pending engine decision considers
        illegal - the log must never contain an unapplyable action.
        """
        pending = self.pending_futures.get(sequence_number)
        if pending is None:
            return
        request, future = pending
        if future.done():
            return
        if payload_type == PAYLOAD_ACTION and request.engine_request is not None:
            if not request.engine_request.is_legal(payload):
                log.warning(
                    'Ignoring illegal action %r for sequence %d (%s)',
                    payload, sequence_number, request.kind,
                )
                return
        future.set_result((payload_type, payload))

    def timeout_forfeit_player(self) -> Optional[int]:
        """The player index that hit the consecutive-timeout limit, if any."""
        for player_index, count in self.consecutive_timeouts.items():
            if count >= CONSECUTIVE_TIMEOUT_FORFEIT_LIMIT:
                return player_index
        return None

    async def _go_live(self) -> None:
        self.replaying = False
        log.info('Replay log exhausted for game %s; going live', self.session.game_id)
        if self.on_go_live is not None:
            await self.on_go_live()
