"""
EngineMatchDriver: the loop that runs one engine_alpha game over the decision
broker. Replaces the old GameFlow turn machinery: the engine owns all rules
and phase sequencing; the driver presents pending decisions, applies the
answers, and narrates the emitted events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from engine_alpha.events import EVENT_PHASE_CHANGED
from zutomayo.match.decisions import MatchDecisionResponse
from zutomayo.match.presentation import build_match_request
from zutomayo.match.state_view import BoardView, project_board_view

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
        on_board_changed: Optional[Callable[[BoardView], Awaitable[None]]] = None,
    ) -> None:
        self.session = session
        self.game = game
        self.broker = broker
        self.narrator = narrator
        self.player_names = player_names
        # Awaited whenever the visible board changed after an apply; the flow
        # renders and sends the board image here.
        self.on_board_changed = on_board_changed

    def _opponent_name(self, player_index: int) -> str:
        return self.player_names.get(1 - player_index, 'opponent')

    async def run_to_completion(self) -> MatchOutcome:
        from zutomayo.match.narrator import zone_signature

        game = self.game
        state = game.state
        state.event_sink = []
        board_view = project_board_view(game, self.player_names)
        previous_signature = zone_signature(board_view)

        while not game.is_terminal():
            engine_request = game.decision_context()
            request = build_match_request(
                game, engine_request,
                opponent_name=self._opponent_name(state.acting),
            )
            response: MatchDecisionResponse = await self.broker.request(request)

            state.event_sink.clear()
            game.apply(response.payload)
            events = list(state.event_sink)
            state.event_sink.clear()

            if any(event[0] == EVENT_PHASE_CHANGED for event in events):
                for adapter in self.broker.adapters.values():
                    on_phase_changed = getattr(adapter, 'on_phase_changed', None)
                    if on_phase_changed is not None:
                        on_phase_changed(state.phase)

            board_view = project_board_view(game, self.player_names)
            await self.narrator.publish(events, board_view)

            signature = zone_signature(board_view)
            if signature != previous_signature and self.on_board_changed is not None:
                await self.on_board_changed(board_view)
            previous_signature = signature

            forfeited_player = self.broker.timeout_forfeit_player()
            if forfeited_player is not None:
                log.info(
                    'Player %d forfeited game %s after consecutive timeouts',
                    forfeited_player, self.session.game_id,
                )
                return MatchOutcome(winner=1 - forfeited_player, forfeited_player=forfeited_player)

        self.narrator.snapshot(board_view)
        return MatchOutcome(winner=state.winner, forfeited_player=None)
