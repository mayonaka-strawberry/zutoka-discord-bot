"""
Persistence layer for user-saved TCG decks.

Thin shim over the TCG deck repository (see deck_repository.py); TCG decks
live in the PostgreSQL decks_tcg table keyed by Discord user id. TCG decks
have 20 main deck cards and 8 side deck cards. The repository singleton is
looked up through the module attribute on every call so tests can swap in an
in-memory fake.
"""

from __future__ import annotations

from zutomayo.data import deck_repository
from zutomayo.data.deck_repository import resolve_card_list
from zutomayo.models.card import Card


async def load_user_tcg_decks(user_id: int) -> list[dict]:
    """Load all TCG decks for a user. Returns [] if none are stored."""
    return await deck_repository.TCG_DECK_REPOSITORY.load_user_decks(user_id)


async def save_user_tcg_decks(user_id: int, decks: list[dict]) -> None:
    """Replace all TCG decks for a user."""
    await deck_repository.TCG_DECK_REPOSITORY.save_user_decks(user_id, decks)


async def get_tcg_deck_names(user_id: int) -> list[str]:
    """Return just the names of all saved TCG decks for a user."""
    return await deck_repository.TCG_DECK_REPOSITORY.get_deck_names(user_id)


async def get_tcg_deck_by_name(user_id: int, name: str) -> dict | None:
    """Find a single TCG deck by name (case-sensitive). Returns None if not found."""
    return await deck_repository.TCG_DECK_REPOSITORY.get_deck_by_name(user_id, name)


async def search_tcg_deck_names(user_id: int, prefix: str, limit: int = 25) -> list[str]:
    """TCG deck names starting with the prefix, for autocomplete."""
    return await deck_repository.TCG_DECK_REPOSITORY.search_deck_names(user_id, prefix, limit)


async def add_tcg_deck(user_id: int, name: str, deck_cards: list[Card], side_deck_cards: list[Card]) -> None:
    """Add a new TCG deck. Raises ValueError if name already exists."""
    await deck_repository.TCG_DECK_REPOSITORY.add_deck(
        user_id, name, {'deck': deck_cards, 'side_deck': side_deck_cards},
    )


async def update_tcg_deck(user_id: int, name: str, deck_cards: list[Card], side_deck_cards: list[Card]) -> None:
    """Replace the cards in an existing TCG deck. Raises ValueError if not found."""
    await deck_repository.TCG_DECK_REPOSITORY.update_deck(
        user_id, name, {'deck': deck_cards, 'side_deck': side_deck_cards},
    )


async def delete_tcg_deck(user_id: int, name: str) -> None:
    """Remove a TCG deck by name. Raises ValueError if not found."""
    await deck_repository.TCG_DECK_REPOSITORY.delete_deck(user_id, name)


def resolve_tcg_deck_cards(
    deck_data: dict,
    card_index: dict[tuple[int, int], Card],
) -> tuple[list[Card], list[Card]]:
    """
    Convert a saved TCG deck's card references to Card objects.

    Returns (main_deck, side_deck) as lists of Card objects.
    Raises ValueError if any card reference is invalid.
    """
    main_cards = resolve_card_list(deck_data, 'deck', card_index)
    side_cards = resolve_card_list(deck_data, 'side_deck', card_index)
    return main_cards, side_cards
