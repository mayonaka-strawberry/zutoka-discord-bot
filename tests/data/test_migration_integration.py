"""
PostgreSQL integration tests for the one-time JSON migration script.

Skipped unless ZUTOKA_TEST_DATABASE_URL is set; see docs/postgresql_setup.md.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.support.database_support import run_with_database
from zutomayo.data import database


def _load_migration_module():
    script_path = REPOSITORY_ROOT / 'scripts' / 'migrate_json_to_postgresql.py'
    specification = importlib.util.spec_from_file_location('migrate_json_to_postgresql', script_path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _write_fixtures(tmp_path: Path, module) -> None:
    decks_directory = tmp_path / 'decks'
    tcg_directory = tmp_path / 'decks_tcg'
    players_directory = tmp_path / 'players'
    decks_directory.mkdir()
    tcg_directory.mkdir()
    players_directory.mkdir()

    (decks_directory / '111.json').write_text(json.dumps({
        'user_id': 111,
        'decks': [{'name': 'Alpha', 'cards': [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 14}]}],
    }), encoding='utf-8')
    (decks_directory / 'not-a-user.json').write_text('{}', encoding='utf-8')
    (tcg_directory / '111.json').write_text(json.dumps({
        'user_id': 111,
        'decks': [{
            'name': 'Series',
            'deck': [{'pack': 1, 'id': 13}],
            'side_deck': [{'pack': 1, 'id': 14}],
        }],
    }), encoding='utf-8')
    (players_directory / 'usernames.json').write_text(json.dumps({
        '111': {'name': 'Alpha Player', 'custom': True},
        '222': {'name': 'Beta Player', 'custom': False},
    }), encoding='utf-8')

    module.DECKS_DIRECTORY = decks_directory
    module.TCG_DECKS_DIRECTORY = tcg_directory
    module.USERNAMES_FILE = players_directory / 'usernames.json'


async def _table_counts() -> dict[str, int]:
    async with database.get_pool().acquire() as connection:
        return {
            table: await connection.fetchval(f'SELECT count(*) FROM {table}')
            for table in ('decks', 'decks_tcg', 'display_names', 'player_profiles')
        }


def test_migration_round_trip_idempotency_and_dry_run(integration_database_url, tmp_path):
    module = _load_migration_module()
    _write_fixtures(tmp_path, module)

    async def exercise():
        # Dry run writes nothing.
        async with database.get_pool().acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            await module.migrate_standard_decks(connection)
            await module.migrate_tcg_decks(connection)
            await module.migrate_display_names(connection)
            await transaction.rollback()
        dry_run_counts = await _table_counts()

        # Real run, twice, must be idempotent.
        for _ in range(2):
            async with database.get_pool().acquire() as connection:
                await module.migrate_standard_decks(connection)
                await module.migrate_tcg_decks(connection)
                await module.migrate_display_names(connection)
        final_counts = await _table_counts()

        async with database.get_pool().acquire() as connection:
            deck_row = await connection.fetchrow('SELECT * FROM decks WHERE user_id = 111')
            tcg_row = await connection.fetchrow('SELECT * FROM decks_tcg WHERE user_id = 111')
            name_row = await connection.fetchrow('SELECT * FROM display_names WHERE user_id = 111')
        return dry_run_counts, final_counts, deck_row, tcg_row, name_row

    dry_run_counts, final_counts, deck_row, tcg_row, name_row = run_with_database(
        integration_database_url, exercise,
    )

    assert dry_run_counts == {'decks': 0, 'decks_tcg': 0, 'display_names': 0, 'player_profiles': 0}
    assert final_counts == {'decks': 1, 'decks_tcg': 1, 'display_names': 2, 'player_profiles': 0}, \
        'profiles are never migrated; duplicate runs never duplicate rows'
    assert deck_row['name'] == 'Alpha'
    assert deck_row['cards'] == [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 14}]
    assert tcg_row['main_deck'] == [{'pack': 1, 'id': 13}]
    assert tcg_row['side_deck'] == [{'pack': 1, 'id': 14}]
    assert name_row['name'] == 'Alpha Player' and name_row['custom'] is True
