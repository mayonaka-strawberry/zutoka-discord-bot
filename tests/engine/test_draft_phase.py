"""Unit tests for the pure draft-phase logic.

Covers box opening, the box/page slicing that lines picker pages up with the
grid images, copy-limit enforcement (max two copies, never more than opened),
and the selected-pick formatting.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.data.deck_validator import get_card_index  # noqa: E402
from zutomayo.match.draft_flow import (  # noqa: E402
    CARDS_PER_BOX,
    MAXIMUM_COPIES_PER_CARD,
    box_page_title,
    enforce_copy_limit,
    format_selected_picks,
    open_draft_boxes,
    total_pages_for_pool,
)

from tests.support.game_state_builder import card_by_identity  # noqa: E402


def test_open_draft_boxes_returns_fifty_cards_per_requested_pack():
    all_cards, _ = get_card_index()
    boxes = open_draft_boxes([1, 3], all_cards)
    assert len(boxes) == 2
    assert all(len(box) == CARDS_PER_BOX for box in boxes)
    assert all(card.pack == 1 for card in boxes[0])
    assert all(card.pack == 3 for card in boxes[1])


def test_total_pages_matches_two_pages_per_box():
    assert total_pages_for_pool(CARDS_PER_BOX) == 2
    assert total_pages_for_pool(2 * CARDS_PER_BOX) == 4
    assert total_pages_for_pool(28) == 2


def test_box_page_title_maps_pages_to_box_halves():
    assert box_page_title(0) == 'Box 1 (1/2)'
    assert box_page_title(1) == 'Box 1 (2/2)'
    assert box_page_title(2) == 'Box 2 (1/2)'
    assert box_page_title(3) == 'Box 2 (2/2)'


def _value_map(cards):
    value_to_card = {}
    values = []
    for index, card in enumerate(cards):
        value = f'{card.pack:02d}-{card.id:03d}#{index}'
        values.append(value)
        value_to_card[value] = card
    return values, value_to_card


def test_enforce_copy_limit_drops_a_third_copy():
    card = card_by_identity('01-013')
    values, value_to_card = _value_map([card, card, card])

    kept, dropped = enforce_copy_limit(values, value_to_card)

    assert len(kept) == MAXIMUM_COPIES_PER_CARD
    assert len(dropped) == 1
    assert dropped[0] is card


def test_enforce_copy_limit_keeps_earlier_values_first():
    card = card_by_identity('01-013')
    values, value_to_card = _value_map([card, card, card])

    # The third value is the one dropped, so the first two survive.
    kept, _ = enforce_copy_limit(values, value_to_card)
    assert kept == {values[0], values[1]}


def test_enforce_copy_limit_allows_distinct_cards():
    first = card_by_identity('01-013')
    second = card_by_identity('01-014')
    values, value_to_card = _value_map([first, first, second, second])

    kept, dropped = enforce_copy_limit(values, value_to_card)

    assert kept == set(values)
    assert dropped == []


def test_format_selected_picks_groups_and_counts():
    first = card_by_identity('01-013')
    second = card_by_identity('01-014')
    lines = format_selected_picks([first, second, first])

    assert lines == [
        f'01-013 {first.name} x2',
        f'01-014 {second.name}',
    ]
