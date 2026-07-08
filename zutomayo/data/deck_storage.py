"""
Persistence layer for user-saved decks.

Thin shim over DeckRepository (see deck_repository.py); each user's decks are
stored in a JSON file at zutomayo/decks/<discord_user_id>.json.
"""

from __future__ import annotations
import json
from pathlib import Path
from zutomayo.data.deck_repository import STANDARD_DECK_REPOSITORY
from zutomayo.models.card import Card


DECKS_DIR = STANDARD_DECK_REPOSITORY.directory
DEFAULT_DECKS_FILE = Path(__file__).resolve().parent.parent / 'default_decks.json'


def load_default_decks() -> list[dict]:
    """Load all default (pre-built) decks from default_decks.json. Returns [] if file missing."""
    if not DEFAULT_DECKS_FILE.exists():
        return []
    with open(DEFAULT_DECKS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('decks', [])


def load_user_decks(user_id: int) -> list[dict]:
    """Load all decks for a user. Returns [] if no file exists."""
    return STANDARD_DECK_REPOSITORY.load_user_decks(user_id)


def save_user_decks(user_id: int, decks: list[dict]) -> None:
    """Write all decks for a user atomically (temp + os.replace) to avoid corruption."""
    STANDARD_DECK_REPOSITORY.save_user_decks(user_id, decks)


def get_deck_names(user_id: int) -> list[str]:
    """Return just the names of all saved decks for a user."""
    return STANDARD_DECK_REPOSITORY.get_deck_names(user_id)


def get_deck_by_name(user_id: int, name: str) -> dict | None:
    """Find a single deck by name (case-sensitive). Returns None if not found."""
    return STANDARD_DECK_REPOSITORY.get_deck_by_name(user_id, name)


def add_deck(user_id: int, name: str, cards: list[Card]) -> None:
    """Add a new deck. Raises ValueError if name already exists."""
    STANDARD_DECK_REPOSITORY.add_deck(user_id, name, {'cards': cards})


def update_deck(user_id: int, name: str, cards: list[Card]) -> None:
    """Replace the cards in an existing deck. Raises ValueError if not found."""
    STANDARD_DECK_REPOSITORY.update_deck(user_id, name, {'cards': cards})


def delete_deck(user_id: int, name: str) -> None:
    """Remove a deck by name. Raises ValueError if not found."""
    STANDARD_DECK_REPOSITORY.delete_deck(user_id, name)


def resolve_deck_cards(
    deck_data: dict,
    card_index: dict[tuple[int, int], Card],
) -> list[Card]:
    """
    Convert a saved deck's card references to Card objects.

    Returns list[Card] of length 20 (with duplicates for copies).
    Raises ValueError if any card reference is invalid.
    """
    return STANDARD_DECK_REPOSITORY.resolve_card_list(deck_data, 'cards', card_index)
