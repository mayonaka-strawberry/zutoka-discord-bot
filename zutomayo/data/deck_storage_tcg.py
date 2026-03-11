"""
Persistence layer for user-saved TCG decks.

Each user's TCG decks are stored in a JSON file at zutomayo/decks_tcg/<discord_user_id>.json.
TCG decks have 20 main deck cards and 8 side deck cards.
"""

from __future__ import annotations
import json
from pathlib import Path
from zutomayo.models.card import Card


TCG_DECKS_DIR = Path(__file__).resolve().parent.parent / 'decks_tcg'


def _user_file(user_id: int) -> Path:
    return TCG_DECKS_DIR / f'{user_id}.json'


def load_user_tcg_decks(user_id: int) -> list[dict]:
    """Load all TCG decks for a user. Returns [] if no file exists."""
    path = _user_file(user_id)
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('decks', [])


def save_user_tcg_decks(user_id: int, decks: list[dict]) -> None:
    """Write all TCG decks for a user to disk."""
    TCG_DECKS_DIR.mkdir(parents=True, exist_ok=True)
    path = _user_file(user_id)
    data = {'user_id': user_id, 'decks': decks}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def get_tcg_deck_names(user_id: int) -> list[str]:
    """Return just the names of all saved TCG decks for a user."""
    return [deck_entry['name'] for deck_entry in load_user_tcg_decks(user_id)]


def get_tcg_deck_by_name(user_id: int, name: str) -> dict | None:
    """Find a single TCG deck by name (case-sensitive). Returns None if not found."""
    for deck_entry in load_user_tcg_decks(user_id):
        if deck_entry['name'] == name:
            return deck_entry
    return None


def add_tcg_deck(user_id: int, name: str, deck_cards: list[Card], side_deck_cards: list[Card]) -> None:
    """Add a new TCG deck. Raises ValueError if name already exists."""
    decks = load_user_tcg_decks(user_id)
    if any(deck_entry['name'] == name for deck_entry in decks):
        raise ValueError(f'A deck named "{name}" already exists.')
    decks.append({
        'name': name,
        'deck': [{'pack': card.pack, 'id': card.id} for card in deck_cards],
        'side_deck': [{'pack': card.pack, 'id': card.id} for card in side_deck_cards],
    })
    save_user_tcg_decks(user_id, decks)


def update_tcg_deck(user_id: int, name: str, deck_cards: list[Card], side_deck_cards: list[Card]) -> None:
    """Replace the cards in an existing TCG deck. Raises ValueError if not found."""
    decks = load_user_tcg_decks(user_id)
    for deck_entry in decks:
        if deck_entry['name'] == name:
            deck_entry['deck'] = [{'pack': card.pack, 'id': card.id} for card in deck_cards]
            deck_entry['side_deck'] = [{'pack': card.pack, 'id': card.id} for card in side_deck_cards]
            save_user_tcg_decks(user_id, decks)
            return
    raise ValueError(f'Deck "{name}" not found.')


def delete_tcg_deck(user_id: int, name: str) -> None:
    """Remove a TCG deck by name. Raises ValueError if not found."""
    decks = load_user_tcg_decks(user_id)
    original_len = len(decks)
    decks = [deck_entry for deck_entry in decks if deck_entry['name'] != name]
    if len(decks) == original_len:
        raise ValueError(f'Deck "{name}" not found.')
    save_user_tcg_decks(user_id, decks)


def resolve_tcg_deck_cards(
    deck_data: dict,
    card_index: dict[tuple[int, int], Card],
) -> tuple[list[Card], list[Card]]:
    """
    Convert a saved TCG deck's card references to Card objects.

    Returns (main_deck, side_deck) as lists of Card objects.
    Raises ValueError if any card reference is invalid.
    """
    main_cards = []
    for entry in deck_data['deck']:
        key = (entry['pack'], entry['id'])
        card = card_index.get(key)
        if card is None:
            raise ValueError(f'Card {key[0]:02d}-{key[1]:03d} not found in card database.')
        main_cards.append(card)

    side_cards = []
    for entry in deck_data['side_deck']:
        key = (entry['pack'], entry['id'])
        card = card_index.get(key)
        if card is None:
            raise ValueError(f'Card {key[0]:02d}-{key[1]:03d} not found in card database.')
        side_cards.append(card)

    return main_cards, side_cards
