"""
Code shared verbatim by the standard and TCG deck views, which are deliberate forks.
"""

from __future__ import annotations

DECKS_PER_PAGE = 25


class DeckNamePaginationMixin:
    """Pagination over self.all_deck_names driven by self.page."""

    all_deck_names: list[str]
    page: int

    @property
    def total_pages(self) -> int:
        return max(1, -(-len(self.all_deck_names) // DECKS_PER_PAGE))

    def _page_slice(self) -> list[str]:
        start = self.page * DECKS_PER_PAGE
        return self.all_deck_names[start : start + DECKS_PER_PAGE]
