"""
DecisionBroker: the single choke point for every interactive game decision.

Flows and the effect engine call ``await broker.request(decision_request)`` and
get back a DecisionResponse. The broker:

- assigns deterministic sequence numbers (incremented at request() entry, in
  code order, so numbering is stable even when both players are prompted
  concurrently),
- during replay (after a bot restart) answers requests instantly from the
  loaded decision log, verifying each request's fingerprint against the log,
- live, presents the decision through the requesting player's DecisionAdapter
  (Discord views, the solo bot agent, or a scripted test adapter) and awaits
  the answer with the request's timeout,
- treats timeouts as decisions (payload_type 'timeout') so replay reproduces
  timeout fallback branches exactly,
- appends every response to the game's persistence log when one is attached.

This module must stay import-light: no discord, no views, no effect engine.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional, Protocol

from zutomayo.engine.decisions import (
    PAYLOAD_TIMEOUT,
    DecisionRequest,
    DecisionResponse,
    request_fingerprint,
)

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession

log = logging.getLogger(__name__)


class DecisionAdapter(Protocol):
    """Presents a decision to whoever answers for one player."""

    async def present_decision(self, session: 'GameSession', request: DecisionRequest) -> None:
        ...


class ResumeDivergenceError(Exception):
    """A replayed request no longer matches the logged decision sequence."""

    def __init__(self, request: DecisionRequest, logged_fingerprint: Optional[dict], live_fingerprint: dict) -> None:
        super().__init__(
            f'Replay diverged at sequence {request.sequence_number}: '
            f'logged {logged_fingerprint!r} vs live {live_fingerprint!r}'
        )
        self.request = request


class DecisionBroker:
    def __init__(
        self,
        session: 'GameSession',
        adapters: dict[int, DecisionAdapter],
        persistence: Any = None,
    ) -> None:
        self.session = session
        self.adapters = adapters
        self.persistence = persistence
        self.issue_counter = 0
        # Loaded on resume: sequence_number -> (fingerprint dict, DecisionResponse)
        self.replay_log: dict[int, tuple[dict, DecisionResponse]] = {}
        self.replaying = False
        self.pending_futures: dict[int, asyncio.Future] = {}
        # Called once when the replay log is exhausted and the game goes live
        # (unmute transport, announce the resume). Installed by the resume
        # manager; a no-op for games that never restarted.
        self.on_go_live: Any = None

    async def request(self, request: DecisionRequest) -> DecisionResponse:
        request.sequence_number = self.issue_counter
        self.issue_counter += 1

        if self.replaying:
            logged = self.replay_log.get(request.sequence_number)
            if logged is not None:
                logged_fingerprint, logged_response = logged
                live_fingerprint = request_fingerprint(request)
                if logged_fingerprint != live_fingerprint:
                    raise ResumeDivergenceError(request, logged_fingerprint, live_fingerprint)
                return logged_response
            await self._go_live()

        adapter = self.adapters[request.player_index]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending_futures[request.sequence_number] = future
        try:
            await adapter.present_decision(self.session, request)
            try:
                payload_type, payload = await asyncio.wait_for(future, request.timeout_seconds)
            except asyncio.TimeoutError:
                payload_type, payload = PAYLOAD_TIMEOUT, None
        finally:
            self.pending_futures.pop(request.sequence_number, None)

        response = DecisionResponse(request.sequence_number, payload_type, payload)
        if self.persistence is not None:
            await self.persistence.append_decision(request, response)
        return response

    def submit(self, sequence_number: int, payload_type: str, payload: Any) -> None:
        """
        Deliver a player's answer. Called by Discord views and the bot adapter.
        Answers for unknown or already-resolved sequence numbers (for example a
        button pressed after the prompt timed out) are ignored.
        """
        future = self.pending_futures.get(sequence_number)
        if future is not None and not future.done():
            future.set_result((payload_type, payload))

    async def _go_live(self) -> None:
        self.replaying = False
        log.info('Replay log exhausted for game %s; going live', self.session.game_id)
        if self.on_go_live is not None:
            await self.on_go_live()
