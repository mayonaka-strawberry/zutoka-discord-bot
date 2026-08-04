"""
EngineMatchDriver: the loop that runs one engine_alpha game over the decision
broker. Replaces the old GameFlow turn machinery: the engine owns all rules
and phase sequencing; the driver presents pending decisions, applies the
answers, and narrates the emitted events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from engine_alpha.events import EVENT_PHASE_CHANGED
from zutomayo.match.decisions import MatchDecisionResponse
from zutomayo.match.gate_presenter import SnapshottingEventSink
from zutomayo.match.presentation import build_match_request
from zutomayo.match.state_view import project_board_view

log = logging.getLogger(__name__)


@dataclass
class MatchOutcome:
    winner: int                       # 0/1 winner index, 2 draw
    forfeited_player: Optional[int]   # player who timed out of the game, if any


class EngineMatchDriver:
    def __init__(
        self,
        session: Any,
        game: Any,
        broker: Any,
        narrator: Any,
        player_names: dict[int, str],
    ) -> None:
        self.session = session
        self.game = game
        self.broker = broker
        self.narrator = narrator
        self.player_names = player_names

    def _opponent_name(self, player_index: int) -> str:
        return self.player_names.get(1 - player_index, 'opponent')

    async def run_to_completion(self) -> MatchOutcome:
        game = self.game
        state = game.state
        # A snapshotting sink so the narrator can show the board as it looked at
        # every phase boundary inside an apply, not just where the apply stopped.
        sink = SnapshottingEventSink(lambda: project_board_view(game, self.player_names))
        state.event_sink = sink
        board_view = project_board_view(game, self.player_names)

        while not game.is_terminal():
            engine_request = game.decision_context()
            request = build_match_request(
                game, engine_request,
                opponent_name=self._opponent_name(state.acting),
            )
            response: MatchDecisionResponse = await self.broker.request(request)

            sink.clear()
            game.apply(response.payload)
            events = list(sink)
            snapshots = dict(sink.snapshots)
            sink.clear()

            if any(event[0] == EVENT_PHASE_CHANGED for event in events):
                for adapter in self.broker.adapters.values():
                    on_phase_changed = getattr(adapter, 'on_phase_changed', None)
                    if on_phase_changed is not None:
                        on_phase_changed(state.phase)

            board_view = project_board_view(game, self.player_names)
            await self.narrator.publish(events, board_view, snapshots)

            forfeited_player = self.broker.timeout_forfeit_player()
            if forfeited_player is not None:
                log.info(
                    'Player %d forfeited game %s after consecutive timeouts',
                    forfeited_player, self.session.game_id,
                )
                return MatchOutcome(winner=1 - forfeited_player, forfeited_player=forfeited_player)

        self.narrator.snapshot(board_view)
        return MatchOutcome(winner=state.winner, forfeited_player=None)
