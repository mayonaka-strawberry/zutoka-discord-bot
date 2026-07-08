"""
Persistence layer for user-saved TCG decks.

Thin shim over DeckRepository (see deck_repository.py); each user's TCG decks
are stored in a JSON file at zutomayo/decks_tcg/<discord_user_id>.json.
TCG decks have 20 main deck cards and 8 side deck cards.
"""

from __future__ import annotations
from zutomayo.data.deck_repository import TCG_DECK_REPOSITORY
from zutomayo.models.card import Card


TCG_DECKS_DIR = TCG_DECK_REPOSITORY.directory


def load_user_tcg_decks(user_id: int) -> list[dict]:
    """Load all TCG decks for a user. Returns [] if no file exists."""
    return TCG_DECK_REPOSITORY.load_user_decks(user_id)


def save_user_tcg_decks(user_id: int, decks: list[dict]) -> None:
    """Write all TCG decks for a user atomically (temp + os.replace) to avoid corruption."""
    TCG_DECK_REPOSITORY.save_user_decks(user_id, decks)


def get_tcg_deck_names(user_id: int) -> list[str]:
    """Return just the names of all saved TCG decks for a user."""
    return TCG_DECK_REPOSITORY.get_deck_names(user_id)


def get_tcg_deck_by_name(user_id: int, name: str) -> dict | None:
    """Find a single TCG deck by name (case-sensitive). Returns None if not found."""
    return TCG_DECK_REPOSITORY.get_deck_by_name(user_id, name)


def add_tcg_deck(user_id: int, name: str, deck_cards: list[Card], side_deck_cards: list[Card]) -> None:
    """Add a new TCG deck. Raises ValueError if name already exists."""
    TCG_DECK_REPOSITORY.add_deck(user_id, name, {'deck': deck_cards, 'side_deck': side_deck_cards})


def update_tcg_deck(user_id: int, name: str, deck_cards: list[Card], side_deck_cards: list[Card]) -> None:
    """Replace the cards in an existing TCG deck. Raises ValueError if not found."""
    TCG_DECK_REPOSITORY.update_deck(user_id, name, {'deck': deck_cards, 'side_deck': side_deck_cards})


def delete_tcg_deck(user_id: int, name: str) -> None:
    """Remove a TCG deck by name. Raises ValueError if not found."""
    TCG_DECK_REPOSITORY.delete_deck(user_id, name)


def resolve_tcg_deck_cards(
    deck_data: dict,
    card_index: dict[tuple[int, int], Card],
) -> tuple[list[Card], list[Card]]:
    """
    Convert a saved TCG deck's card references to Card objects.

    Returns (main_deck, side_deck) as lists of Card objects.
    Raises ValueError if any card reference is invalid.
    """
    main_cards = TCG_DECK_REPOSITORY.resolve_card_list(deck_data, 'deck', card_index)
    side_cards = TCG_DECK_REPOSITORY.resolve_card_list(deck_data, 'side_deck', card_index)
    return main_cards, side_cards
