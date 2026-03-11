"""
Persistence layer for user-saved decks.

Each user's decks are stored in a JSON file at zutomayo/decks/<discord_user_id>.json.
"""

from __future__ import annotations
import json
from pathlib import Path
from zutomayo.models.card import Card


DECKS_DIR = Path(__file__).resolve().parent.parent / 'decks'


def _user_file(user_id: int) -> Path:
    return DECKS_DIR / f'{user_id}.json'


def load_user_decks(user_id: int) -> list[dict]:
    """Load all decks for a user. Returns [] if no file exists."""
    path = _user_file(user_id)
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('decks', [])


def save_user_decks(user_id: int, decks: list[dict]) -> None:
    """Write all decks for a user to disk."""
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    path = _user_file(user_id)
    data = {'user_id': user_id, 'decks': decks}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def get_deck_names(user_id: int) -> list[str]:
    """Return just the names of all saved decks for a user."""
    return [deck_entry['name'] for deck_entry in load_user_decks(user_id)]


def get_deck_by_name(user_id: int, name: str) -> dict | None:
    """Find a single deck by name (case-sensitive). Returns None if not found."""
    for deck_entry in load_user_decks(user_id):
        if deck_entry['name'] == name:
            return deck_entry
    return None


def add_deck(user_id: int, name: str, cards: list[Card]) -> None:
    """Add a new deck. Raises ValueError if name already exists."""
    decks = load_user_decks(user_id)
    if any(deck_entry['name'] == name for deck_entry in decks):
        raise ValueError(f'A deck named "{name}" already exists.')
    decks.append({
        'name': name,
        'cards': [{'pack': card.pack, 'id': card.id} for card in cards],
    })
    save_user_decks(user_id, decks)


def update_deck(user_id: int, name: str, cards: list[Card]) -> None:
    """Replace the cards in an existing deck. Raises ValueError if not found."""
    decks = load_user_decks(user_id)
    for deck_entry in decks:
        if deck_entry['name'] == name:
            deck_entry['cards'] = [{'pack': card.pack, 'id': card.id} for card in cards]
            save_user_decks(user_id, decks)
            return
    raise ValueError(f'Deck "{name}" not found.')


def delete_deck(user_id: int, name: str) -> None:
    """Remove a deck by name. Raises ValueError if not found."""
    decks = load_user_decks(user_id)
    original_len = len(decks)
    decks = [deck_entry for deck_entry in decks if deck_entry['name'] != name]
    if len(decks) == original_len:
        raise ValueError(f'Deck "{name}" not found.')
    save_user_decks(user_id, decks)


def resolve_deck_cards(
    deck_data: dict,
    card_index: dict[tuple[int, int], Card],
) -> list[Card]:
    """
    Convert a saved deck's card references to Card objects.

    Returns list[Card] of length 20 (with duplicates for copies).
    Raises ValueError if any card reference is invalid.
    """
    cards = []
    for entry in deck_data['cards']:
        key = (entry['pack'], entry['id'])
        card = card_index.get(key)
        if card is None:
            raise ValueError(f'Card {key[0]:02d}-{key[1]:03d} not found in card database.')
        cards.append(card)
    return cards
