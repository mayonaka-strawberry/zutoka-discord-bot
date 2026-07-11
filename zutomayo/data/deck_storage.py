"""
Persistence layer for user-saved decks.

Thin shim over the standard deck repository (see deck_repository.py); decks
live in the PostgreSQL decks table keyed by Discord user id. The repository
singleton is looked up through the module attribute on every call so tests
can swap in an in-memory fake.
"""

from __future__ import annotations
import json
from pathlib import Path

from zutomayo.data import deck_repository
from zutomayo.data.deck_repository import resolve_card_list
from zutomayo.models.card import Card


DEFAULT_DECKS_FILE = Path(__file__).resolve().parent.parent / 'default_decks.json'


def load_default_decks() -> list[dict]:
    """Load all default (pre-built) decks from default_decks.json. Returns [] if file missing."""
    if not DEFAULT_DECKS_FILE.exists():
        return []
    with open(DEFAULT_DECKS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('decks', [])


async def load_user_decks(user_id: int) -> list[dict]:
    """Load all decks for a user. Returns [] if none are stored."""
    return await deck_repository.STANDARD_DECK_REPOSITORY.load_user_decks(user_id)


async def save_user_decks(user_id: int, decks: list[dict]) -> None:
    """Replace all decks for a user."""
    await deck_repository.STANDARD_DECK_REPOSITORY.save_user_decks(user_id, decks)


async def get_deck_names(user_id: int) -> list[str]:
    """Return just the names of all saved decks for a user."""
    return await deck_repository.STANDARD_DECK_REPOSITORY.get_deck_names(user_id)


async def get_deck_by_name(user_id: int, name: str) -> dict | None:
    """Find a single deck by name (case-sensitive). Returns None if not found."""
    return await deck_repository.STANDARD_DECK_REPOSITORY.get_deck_by_name(user_id, name)


async def search_deck_names(user_id: int, prefix: str, limit: int = 25) -> list[str]:
    """Deck names starting with the prefix, for autocomplete."""
    return await deck_repository.STANDARD_DECK_REPOSITORY.search_deck_names(user_id, prefix, limit)


async def add_deck(user_id: int, name: str, cards: list[Card]) -> None:
    """Add a new deck. Raises ValueError if name already exists."""
    await deck_repository.STANDARD_DECK_REPOSITORY.add_deck(user_id, name, {'cards': cards})


async def update_deck(user_id: int, name: str, cards: list[Card]) -> None:
    """Replace the cards in an existing deck. Raises ValueError if not found."""
    await deck_repository.STANDARD_DECK_REPOSITORY.update_deck(user_id, name, {'cards': cards})


async def delete_deck(user_id: int, name: str) -> None:
    """Remove a deck by name. Raises ValueError if not found."""
    await deck_repository.STANDARD_DECK_REPOSITORY.delete_deck(user_id, name)


def resolve_deck_cards(
    deck_data: dict,
    card_index: dict[tuple[int, int], Card],
) -> list[Card]:
    """
    Convert a saved deck's card references to Card objects.

    Returns list[Card] of length 20 (with duplicates for copies).
    Raises ValueError if any card reference is invalid.
    """
    return resolve_card_list(deck_data, 'cards', card_index)
