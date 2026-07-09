"""Construction smoke tests for the paginated deck management views.

Regression cover for the Stage 7 dedup: `@property` must live on the mixin's
`total_pages`, not on the views' `_build_page`. Interaction callbacks are
deliberately untested here (manual playtests own that surface); these tests
prove the views build and paginate.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import discord  # noqa: E402
import pytest  # noqa: E402

from zutomayo.ui.deck_management_common import DECKS_PER_PAGE  # noqa: E402
from zutomayo.ui.deck_management_views import ManageDecksView, SavedDeckSelectView  # noqa: E402
from zutomayo.ui.deck_management_views_tcg import ManageDecksTcgView, TcgSavedDeckSelectView  # noqa: E402

SINGLE_PAGE_NAMES = ['Only Deck']
TWO_PAGE_NAMES = [f'Deck {index}' for index in range(DECKS_PER_PAGE + 5)]


class StubSession:
    """SavedDeckSelectView only stores the session at construction time."""


def _manage_view(view_class, deck_names):
    return view_class(user_id=1, deck_names=deck_names, card_index={})


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
    pytest.param(lambda names: _manage_view(ManageDecksView, names), id='ManageDecksView'),
    pytest.param(lambda names: _manage_view(ManageDecksTcgView, names), id='ManageDecksTcgView'),
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
