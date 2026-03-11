from __future__ import annotations

from constants import MIDNIGHT, NIGHT_END
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional
from uuid import uuid4
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.phase import Phase
from zutomayo.enums.result import Result
from zutomayo.models.player import Player

if TYPE_CHECKING:
    from zutomayo.models.card import Card


@dataclass
class GameState:
    game_id: str = field(default_factory=lambda: str(uuid4()))
    players: list[Player] = field(default_factory=list)
    chronos: int = MIDNIGHT
    chronos_at_turn_start: int = MIDNIGHT
    turn: int = 0
    current_phase: Phase = Phase.SETUP
    last_battle_winner: Optional[str] = None
    result: Result = Result.IN_PROGRESS
    # Tracks what character was in each player's battle zone last turn (for effect 02-010)
    previous_battle_characters: dict[int, Optional[Card]] = field(
        default_factory=lambda: {0: None, 1: None}
    )

    @property
    def day_night(self) -> Chronos:
        if 0 <= self.chronos <= NIGHT_END:
            return Chronos.NIGHT
        else:
            return Chronos.DAY

    @property
    def priority_player(self) -> int:
        if self.day_night == Chronos.NIGHT:
            if self.players[0].side == Chronos.NIGHT:
                return 0
            else:
                return 1
        else:
            if self.players[0].side == Chronos.DAY:
                return 0
            else:
                return 1
