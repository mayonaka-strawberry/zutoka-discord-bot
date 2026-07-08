"""
Parametrized persistence for user-saved decks.

One implementation serves both deck formats; the two formats differ only in
storage directory and the card-list fields each deck entry carries:

- standard decks: zutomayo/decks/<user_id>.json, entries {'name', 'cards'}
- TCG decks: zutomayo/decks_tcg/<user_id>.json, entries {'name', 'deck', 'side_deck'}

deck_storage.py and deck_storage_tcg.py remain as thin delegating shims so the
existing import sites keep working unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from zutomayo.models.card import Card

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class DeckRepository:
    def __init__(self, directory: Path, card_list_fields: tuple[str, ...]) -> None:
        self.directory = directory
        self.card_list_fields = card_list_fields

    def _user_file(self, user_id: int) -> Path:
        return self.directory / f'{user_id}.json'

    def load_user_decks(self, user_id: int) -> list[dict]:
        """Load all decks for a user. Returns [] if no file exists."""
        path = self._user_file(user_id)
        if not path.exists():
            return []
        with open(path, 'r', encoding='utf-8') as file_handle:
            data = json.load(file_handle)
        return data.get('decks', [])

    def save_user_decks(self, user_id: int, decks: list[dict]) -> None:
        """Write all decks for a user atomically (temp + os.replace) to avoid corruption."""
        self.directory.mkdir(parents=True, exist_ok=True)
        final_path = self._user_file(user_id)
        temp_path = final_path.with_suffix('.json.tmp')
        data = {'user_id': user_id, 'decks': decks}
        with open(temp_path, 'w', encoding='utf-8') as file_handle:
            json.dump(data, file_handle, indent=2)
        os.replace(temp_path, final_path)

    def get_deck_names(self, user_id: int) -> list[str]:
        return [deck_entry['name'] for deck_entry in self.load_user_decks(user_id)]

    def get_deck_by_name(self, user_id: int, name: str) -> dict | None:
        for deck_entry in self.load_user_decks(user_id):
            if deck_entry['name'] == name:
                return deck_entry
        return None

    @staticmethod
    def _serialize_cards(cards: list[Card]) -> list[dict]:
        return [{'pack': card.pack, 'id': card.id} for card in cards]

    def add_deck(self, user_id: int, name: str, card_lists: dict[str, list[Card]]) -> None:
        """Add a new deck. Raises ValueError if name already exists."""
        decks = self.load_user_decks(user_id)
        if any(deck_entry['name'] == name for deck_entry in decks):
            raise ValueError(f'A deck named "{name}" already exists.')
        entry: dict = {'name': name}
        for field in self.card_list_fields:
            entry[field] = self._serialize_cards(card_lists[field])
        decks.append(entry)
        self.save_user_decks(user_id, decks)

    def update_deck(self, user_id: int, name: str, card_lists: dict[str, list[Card]]) -> None:
        """Replace the cards in an existing deck. Raises ValueError if not found."""
        decks = self.load_user_decks(user_id)
        for deck_entry in decks:
            if deck_entry['name'] == name:
                for field in self.card_list_fields:
                    deck_entry[field] = self._serialize_cards(card_lists[field])
                self.save_user_decks(user_id, decks)
                return
        raise ValueError(f'Deck "{name}" not found.')

    def delete_deck(self, user_id: int, name: str) -> None:
        """Remove a deck by name. Raises ValueError if not found."""
        decks = self.load_user_decks(user_id)
        original_length = len(decks)
        decks = [deck_entry for deck_entry in decks if deck_entry['name'] != name]
        if len(decks) == original_length:
            raise ValueError(f'Deck "{name}" not found.')
        self.save_user_decks(user_id, decks)

    @staticmethod
    def resolve_card_list(
        deck_data: dict,
        field: str,
        card_index: dict[tuple[int, int], Card],
    ) -> list[Card]:
        """Convert one stored card-reference list into Card objects."""
        cards = []
        for entry in deck_data[field]:
            key = (entry['pack'], entry['id'])
            card = card_index.get(key)
            if card is None:
                raise ValueError(f'Card {key[0]:02d}-{key[1]:03d} not found in card database.')
            cards.append(card)
        return cards


STANDARD_DECK_REPOSITORY = DeckRepository(_PACKAGE_ROOT / 'decks', ('cards',))
TCG_DECK_REPOSITORY = DeckRepository(_PACKAGE_ROOT / 'decks_tcg', ('deck', 'side_deck'))
