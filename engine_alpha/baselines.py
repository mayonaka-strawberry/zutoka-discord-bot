"""Baseline agents for evaluation gates."""

from __future__ import annotations

import random

from .actions import SELECT_CARD, SELECT_IDENTITY
from .battle import get_effective_attack, total_power, effective_power_cost
from .cards import (
    ATK_DAY_T, ATK_NIGHT_T, CARD_TYPE_T, POWER_COST_T, SEND_TO_POWER_T,
    TYPE_CHARACTER,
)


class RandomAgent:
    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)

    def act(self, game) -> int:
        return self.rng.choice(game.legal_actions())


class GreedyHeuristicAgent:
    """1-ply heuristic: prefers playable characters with the highest current
    attack for sets/picks; drafts characters with high stats; answers numbers
    at the maximum; never passes when it can act."""

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)

    def act(self, game) -> int:
        request = game.decision_context()
        state = game.state
        acting = state.acting
        legal = game.legal_actions()

        if request.kind == SELECT_IDENTITY:
            def draft_score(def_index: int) -> float:
                is_character = CARD_TYPE_T[def_index] == TYPE_CHARACTER
                stat = max(ATK_DAY_T[def_index], ATK_NIGHT_T[def_index])
                affordability = 8 - POWER_COST_T[def_index]
                return (100 if is_character else 0) + stat + 3 * affordability \
                    + 10 * SEND_TO_POWER_T[def_index]
            return max(legal, key=draft_score)

        if request.kind == SELECT_CARD:
            player = state.players[acting]
            power = total_power(state, player)
            night = state.is_night

            def card_score(action: int) -> float:
                if request.is_pass(action):
                    return -1000.0
                instance_id = request.candidates[action]
                def_index = state.inst_def[instance_id]
                base = ATK_NIGHT_T[def_index] if night else ATK_DAY_T[def_index]
                affordable = power >= effective_power_cost(state, instance_id)
                is_character = CARD_TYPE_T[def_index] == TYPE_CHARACTER
                return base * (1.0 if affordable else 0.3) + (20 if is_character else 0)
            return max(legal, key=card_score)

        # numbers/binary: take the largest option (reveal max, advance max...)
        return legal[-1]
