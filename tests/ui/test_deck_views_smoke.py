"""Construction smoke tests for the deck management views.

The mid-game saved-deck pickers stay paginated (regression cover for the
mixin's `total_pages` property); the managedecks commands now use per-deck
action views selected through autocomplete, and the edit modals are
pre-filled with the deck's current card ids.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import discord  # noqa: E402
import pytest  # noqa: E402

from zutomayo.data.deck_validator import get_card_index  # noqa: E402
from zutomayo.ui.deck_management_common import DECKS_PER_PAGE  # noqa: E402
from zutomayo.ui.deck_management_views import (  # noqa: E402
    EditDeckModal,
    ManageDeckActionsView,
    SavedDeckSelectView,
    format_card_ids_line,
)
from zutomayo.ui.deck_management_views_tcg import (  # noqa: E402
    EditDeckTcgModal,
    ManageDeckTcgActionsView,
    TcgSavedDeckSelectView,
)

from tests.support.cards import card_by_identity  # noqa: E402

SINGLE_PAGE_NAMES = ['Only Deck']
TWO_PAGE_NAMES = [f'Deck {index}' for index in range(DECKS_PER_PAGE + 5)]


class StubSession:
    """SavedDeckSelectView only stores the session at construction time."""


def _select_view(view_class, deck_names):
    return view_class(
        session=StubSession(),
        player_index=0,
        user_id=1,
        deck_names=deck_names,
        card_index={},
        all_cards=[],
    )


VIEW_BUILDERS = [
    pytest.param(lambda names: _select_view(SavedDeckSelectView, names), id='SavedDeckSelectView'),
    pytest.param(lambda names: _select_view(TcgSavedDeckSelectView, names), id='TcgSavedDeckSelectView'),
]


@pytest.mark.parametrize('build_view', VIEW_BUILDERS)
class TestPaginatedDeckViews:
    def test_single_page_has_no_pagination_buttons(self, build_view):
        view = build_view(SINGLE_PAGE_NAMES)
        assert isinstance(view.total_pages, int)
        assert view.total_pages == 1
        labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
        assert '<< Prev' not in labels and 'Next >>' not in labels

    def test_multiple_pages_add_pagination_buttons(self, build_view):
        view = build_view(TWO_PAGE_NAMES)
        assert view.total_pages == 2
        labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
        assert '<< Prev' in labels and 'Next >>' in labels
        select = next(item for item in view.children if isinstance(item, discord.ui.Select))
        assert len(select.options) == DECKS_PER_PAGE


@pytest.mark.parametrize('view_class', [ManageDeckActionsView, ManageDeckTcgActionsView])
def test_manage_actions_view_offers_edit_and_delete(view_class):
    view = view_class(user_id=1, deck_name='My Deck', card_index={})
    labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
    assert labels == ['Edit', 'Delete']


class TestEditModalPrefill:
    def test_standard_edit_modal_is_prefilled_with_current_cards(self):
        _, card_index = get_card_index()
        cards = [card_by_identity('01-013')] * 10 + [card_by_identity('01-014')] * 10
        modal = EditDeckModal('My Deck', user_id=1, card_index=card_index, current_cards=cards)

        expected = format_card_ids_line(cards)
        assert modal.deck_input.default == expected
        assert len(expected) == 20 * 6 + 19, '20 cards render as the 139-character id line'

    def test_tcg_edit_modal_prefills_both_inputs(self):
        _, card_index = get_card_index()
        main_cards = [card_by_identity('01-013')] * 20
        side_cards = [card_by_identity('01-014')] * 8
        modal = EditDeckTcgModal(
            'Series Deck', user_id=1, card_index=card_index,
            current_main_cards=main_cards, current_side_cards=side_cards,
        )

        assert modal.deck_input.default == format_card_ids_line(main_cards)
        assert modal.side_deck_input.default == format_card_ids_line(side_cards)

    def test_prefill_is_per_instance_not_class_level(self):
        _, card_index = get_card_index()
        cards = [card_by_identity('01-013')] * 20
        prefilled = EditDeckModal('A', user_id=1, card_index=card_index, current_cards=cards)
        blank = EditDeckModal('B', user_id=1, card_index=card_index)
        assert prefilled.deck_input.default is not None
        assert blank.deck_input.default is None
