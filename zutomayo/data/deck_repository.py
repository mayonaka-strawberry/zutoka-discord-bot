"""
Parametrized persistence for user-saved decks.

One implementation serves both deck formats; the two formats differ only in
table and the card-list fields each deck entry carries:

- standard decks: decks table, entries {'name', 'cards'}
- TCG decks: decks_tcg table, entries {'name', 'deck', 'side_deck'}

Deck entries keep the historical dict shape ({'name', <card list fields>} with
cards as {'pack', 'id'} references), so callers and the card resolution
helpers are unchanged. Decks are listed alphabetically by name.

deck_storage.py and deck_storage_tcg.py remain as thin delegating shims so the
existing import sites keep working (now awaited). Tests swap the module-level
repository singletons for in-memory fakes.
"""

from __future__ import annotations

from typing import Optional

from zutomayo.models.card import Card


def serialize_cards(cards: list[Card]) -> list[dict]:
    return [{'pack': card.pack, 'id': card.id} for card in cards]


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


class PostgresDeckRepository:
    """
    column_map maps each deck-entry field to its database column, e.g.
    {'cards': 'cards'} for standard decks and
    {'deck': 'main_deck', 'side_deck': 'side_deck'} for TCG decks.
    """

    # Kept on the class as well so repository handles can resolve cards
    # without importing the module function.
    resolve_card_list = staticmethod(resolve_card_list)

    def __init__(self, table: str, column_map: dict[str, str]) -> None:
        self.table = table
        self.column_map = column_map
        self.card_list_fields = tuple(column_map)
        self._select_columns = ', '.join(['name'] + list(column_map.values()))

    # Table and column names are internal constants, never user input.

    def _entry_from_row(self, row) -> dict:
        entry: dict = {'name': row['name']}
        for field, column in self.column_map.items():
            entry[field] = row[column]
        return entry

    async def load_user_decks(self, user_id: int) -> list[dict]:
        """Load all decks for a user, alphabetically by name. Returns [] if none."""
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                f'SELECT {self._select_columns} FROM {self.table} WHERE user_id = $1 ORDER BY name',
                user_id,
            )
        return [self._entry_from_row(row) for row in rows]

    async def save_user_decks(self, user_id: int, decks: list[dict]) -> None:
        """Replace all of a user's decks in one transaction."""
        from zutomayo.data.database import get_pool

        columns = list(self.column_map.values())
        placeholders = ', '.join(f'${position}' for position in range(3, 3 + len(columns)))
        async with get_pool().acquire() as connection:
            async with connection.transaction():
                await connection.execute(f'DELETE FROM {self.table} WHERE user_id = $1', user_id)
                for deck_entry in decks:
                    await connection.execute(
                        f'INSERT INTO {self.table} (user_id, name, {", ".join(columns)}) '
                        f'VALUES ($1, $2, {placeholders})',
                        user_id, deck_entry['name'],
                        *[deck_entry[field] for field in self.card_list_fields],
                    )

    async def get_deck_names(self, user_id: int) -> list[str]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                f'SELECT name FROM {self.table} WHERE user_id = $1 ORDER BY name', user_id,
            )
        return [row['name'] for row in rows]

    async def get_deck_by_name(self, user_id: int, name: str) -> Optional[dict]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            row = await connection.fetchrow(
                f'SELECT {self._select_columns} FROM {self.table} WHERE user_id = $1 AND name = $2',
                user_id, name,
            )
        return self._entry_from_row(row) if row is not None else None

    async def search_deck_names(self, user_id: int, prefix: str, limit: int = 25) -> list[str]:
        """Deck names starting with the prefix (case insensitive), for autocomplete."""
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                f'''
                SELECT name FROM {self.table}
                WHERE user_id = $1 AND lower(name) LIKE lower($2) || '%'
                ORDER BY name LIMIT $3
                ''',
                user_id, prefix, limit,
            )
        return [row['name'] for row in rows]

    async def add_deck(self, user_id: int, name: str, card_lists: dict[str, list[Card]]) -> None:
        """Add a new deck. Raises ValueError if name already exists."""
        from zutomayo.data.database import get_pool

        columns = list(self.column_map.values())
        placeholders = ', '.join(f'${position}' for position in range(3, 3 + len(columns)))
        async with get_pool().acquire() as connection:
            result = await connection.execute(
                f'INSERT INTO {self.table} (user_id, name, {", ".join(columns)}) '
                f'VALUES ($1, $2, {placeholders}) ON CONFLICT (user_id, name) DO NOTHING',
                user_id, name,
                *[serialize_cards(card_lists[field]) for field in self.card_list_fields],
            )
        if result == 'INSERT 0 0':
            raise ValueError(f'A deck named "{name}" already exists.')

    async def update_deck(self, user_id: int, name: str, card_lists: dict[str, list[Card]]) -> None:
        """Replace the cards in an existing deck. Raises ValueError if not found."""
        from zutomayo.data.database import get_pool

        assignments = ', '.join(
            f'{column} = ${position}'
            for position, column in enumerate(self.column_map.values(), start=3)
        )
        async with get_pool().acquire() as connection:
            result = await connection.execute(
                f'UPDATE {self.table} SET {assignments}, updated_at = now() '
                f'WHERE user_id = $1 AND name = $2',
                user_id, name,
                *[serialize_cards(card_lists[field]) for field in self.card_list_fields],
            )
        if result == 'UPDATE 0':
            raise ValueError(f'Deck "{name}" not found.')

    async def delete_deck(self, user_id: int, name: str) -> None:
        """Remove a deck by name. Raises ValueError if not found."""
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            result = await connection.execute(
                f'DELETE FROM {self.table} WHERE user_id = $1 AND name = $2', user_id, name,
            )
        if result == 'DELETE 0':
            raise ValueError(f'Deck "{name}" not found.')

    async def list_all_decks(self) -> list[dict]:
        """Every deck of every user (entries include 'user_id'). Used by maintenance tooling."""
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                f'SELECT user_id, {self._select_columns} FROM {self.table} ORDER BY user_id, name',
            )
        entries = []
        for row in rows:
            entry = self._entry_from_row(row)
            entry['user_id'] = row['user_id']
            entries.append(entry)
        return entries


STANDARD_DECK_REPOSITORY = PostgresDeckRepository('decks', {'cards': 'cards'})
TCG_DECK_REPOSITORY = PostgresDeckRepository(
    'decks_tcg', {'deck': 'main_deck', 'side_deck': 'side_deck'},
)
