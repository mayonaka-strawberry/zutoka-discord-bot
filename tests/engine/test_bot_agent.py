"""Unit tests for BotAgent's deterministic surface: random-mode choice
validity and deck loading fallbacks."""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.data.deck_validator import get_card_index  # noqa: E402
from zutomayo.engine.bot_agent import (  # noqa: E402
    BotAgent,
    load_random_best_deck_v2,
    load_random_saved_deck,
)
from zutomayo.models.card_instance import CardInstance  # noqa: E402

from tests.support.game_state_builder import card_by_identity  # noqa: E402


def _hand(count: int) -> list[CardInstance]:
    return [CardInstance(card=card_by_identity('01-001')) for _ in range(count)]


class TestRandomBotAgent:
    def test_choices_are_always_valid(self):
        random.seed(11)
        agent = BotAgent()
        hand = _hand(5)

        for _ in range(20):
            redraw = agent.choose_redraw(hand)
            assert all(card in hand for card in redraw)
            assert len(redraw) <= 3

            assert agent.choose_initial_battle_card(hand) in hand

            set_cards = agent.choose_cards_to_set(hand, 2)
            assert len(set_cards) == 2 and all(card in hand for card in set_cards)

            order = agent.choose_effect_order(hand)
            assert sorted(map(id, order)) == sorted(map(id, hand))

            assert agent.choose_effect_card(hand) in hand
            assert 1 <= agent.choose_effect_number(1, 5) <= 5
            assert agent.choose_effect_text() is None

    def test_empty_inputs(self):
        agent = BotAgent()
        assert agent.choose_redraw([]) == []
        assert agent.choose_cards_to_set([], 2) == []
        assert agent.choose_effect_card([]) is None


class TestDeckLoading:
    def test_load_random_saved_deck_returns_twenty_known_cards(self):
        random.seed(3)
        _, card_index = get_card_index()
        deck = load_random_saved_deck(card_index)
        assert len(deck) == 20
        assert all((card.pack, card.id) in card_index for card in deck)

    def test_load_random_best_deck_v2_returns_twenty_known_cards(self):
        random.seed(3)
        _, card_index = get_card_index()
        deck = load_random_best_deck_v2(card_index)
        assert len(deck) == 20
        assert all((card.pack, card.id) in card_index for card in deck)