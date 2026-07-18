"""
PostgreSQL integration tests for the deck repositories.

Skipped unless ZUTOKA_TEST_DATABASE_URL is set; see docs/postgresql_setup.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest

from tests.support.database_support import run_with_database
from tests.support.cards import card_by_identity
from zutomayo.data.deck_repository import PostgresDeckRepository


def test_standard_deck_round_trip(integration_database_url):
    repository = PostgresDeckRepository('decks', {'cards': 'cards'})

    async def round_trip():
        cards = [card_by_identity('01-013'), card_by_identity('01-014')]
        await repository.add_deck(7, 'My Deck', {'cards': cards})
        with pytest.raises(ValueError):
            await repository.add_deck(7, 'My Deck', {'cards': cards})

        stored = await repository.get_deck_by_name(7, 'My Deck')
        assert stored['cards'] == [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 14}]

        await repository.update_deck(7, 'My Deck', {'cards': [card_by_identity('01-017')]})
        updated = await repository.get_deck_by_name(7, 'My Deck')
        assert updated['cards'] == [{'pack': 1, 'id': 17}]

        with pytest.raises(ValueError):
            await repository.update_deck(7, 'Missing', {'cards': []})

        await repository.delete_deck(7, 'My Deck')
        assert await repository.get_deck_names(7) == []
        with pytest.raises(ValueError):
            await repository.delete_deck(7, 'My Deck')

    run_with_database(integration_database_url, round_trip)


def test_tcg_deck_columns_and_search(integration_database_url):
    repository = PostgresDeckRepository(
        'decks_tcg', {'deck': 'main_deck', 'side_deck': 'side_deck'},
    )

    async def exercise():
        await repository.add_deck(7, 'Night Deck', {
            'deck': [card_by_identity('01-013')],
            'side_deck': [card_by_identity('01-014')],
        })
        await repository.add_deck(7, 'Noon Deck', {'deck': [], 'side_deck': []})
        await repository.add_deck(8, 'Night Deck', {'deck': [], 'side_deck': []})

        stored = await repository.get_deck_by_name(7, 'Night Deck')
        assert stored['deck'] == [{'pack': 1, 'id': 13}]
        assert stored['side_deck'] == [{'pack': 1, 'id': 14}]

        assert await repository.search_deck_names(7, 'ni') == ['Night Deck']
        assert await repository.get_deck_names(7) == ['Night Deck', 'Noon Deck']

        all_decks = await repository.list_all_decks()
        assert [(entry['user_id'], entry['name']) for entry in all_decks] == [
            (7, 'Night Deck'), (7, 'Noon Deck'), (8, 'Night Deck'),
        ]

    run_with_database(integration_database_url, exercise)


def test_save_user_decks_replaces_everything(integration_database_url):
    repository = PostgresDeckRepository('decks', {'cards': 'cards'})

    async def replace():
        await repository.add_deck(7, 'Old Deck', {'cards': []})
        await repository.save_user_decks(7, [
            {'name': 'New Deck', 'cards': [{'pack': 1, 'id': 13}]},
        ])
        return await repository.load_user_decks(7)

    decks = run_with_database(integration_database_url, replace)
    assert decks == [{'name': 'New Deck', 'cards': [{'pack': 1, 'id': 13}]}]
