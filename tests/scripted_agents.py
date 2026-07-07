"""
Deterministic scripted agents for the regression harnesses.

ScriptedVarietyAgent makes varied but fully deterministic choices driven by an
internal counter. It deliberately consumes NO module-level random state: the
regression baselines depend on the module random stream being identical before
and after the Stage 3 refactor moves RNG ownership, which only holds if the
agents never draw from it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from zutomayo.engine.bot_agent import BotAgent

if TYPE_CHECKING:
    from zutomayo.models.card_instance import CardInstance


class ScriptedVarietyAgent(BotAgent):
    """Counter-driven agent covering a variety of decision branches."""

    def __init__(self, seed_offset: int) -> None:
        super().__init__()
        self.decision_counter = seed_offset

    def _next(self) -> int:
        self.decision_counter += 1
        return self.decision_counter

    def choose_redraw(self, hand: list['CardInstance']) -> list['CardInstance']:
        if not hand:
            return []
        counter = self._next()
        if counter % 2 != 0:
            return []
        return hand[: counter % (len(hand) + 1)]

    def choose_initial_battle_card(self, hand: list['CardInstance']) -> 'CardInstance':
        return hand[self._next() % len(hand)]

    def choose_cards_to_set(
        self, hand: list['CardInstance'], max_cards: int,
    ) -> list['CardInstance']:
        if not hand or max_cards <= 0:
            return []
        count = 1 + (self._next() % min(max_cards, len(hand)))
        return hand[:count]

    def choose_effect_order(
        self, eligible: list['CardInstance'],
    ) -> list['CardInstance']:
        if not eligible:
            return []
        rotation = self._next() % len(eligible)
        return list(eligible[rotation:]) + list(eligible[:rotation])

    def choose_effect_card(
        self, cards: list['CardInstance'],
    ) -> Optional['CardInstance']:
        if not cards:
            return None
        return cards[self._next() % len(cards)]

    def choose_effect_number(self, min_value: int, max_value: int) -> int:
        span = max_value - min_value + 1
        if span <= 0:
            return min_value
        return min_value + (self._next() % span)

    def choose_effect_text(self) -> Optional[str]:
        # Deterministic None exercises every text-input fallback branch.
        return None
