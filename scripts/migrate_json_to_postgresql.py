"""
One-time migration of the JSON storage into PostgreSQL.

Migrates:
- zutomayo/decks/<user_id>.json      -> decks
- zutomayo/decks_tcg/<user_id>.json  -> decks_tcg
- zutomayo/players/usernames.json    -> display_names

Player profiles (Elo, win/loss, matchup stats) are intentionally NOT
migrated: the cutover is a fresh start for player statistics. The JSON files
are left untouched; archive them manually after verifying the migration
(see docs/postgresql_setup.md for the full cutover procedure, which starts
with pulling the latest main so this data is current).

Usage:
    python scripts/migrate_json_to_postgresql.py [--database-url ...] [--dry-run]

The migration is idempotent: rows are upserted, so running it again after a
partial failure (or with newer JSON data) is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

from dotenv import load_dotenv

from zutomayo.data import database

PACKAGE_ROOT = REPOSITORY_ROOT / 'zutomayo'
DECKS_DIRECTORY = PACKAGE_ROOT / 'decks'
TCG_DECKS_DIRECTORY = PACKAGE_ROOT / 'decks_tcg'
USERNAMES_FILE = PACKAGE_ROOT / 'players' / 'usernames.json'


def _load_json(path: Path):
    with open(path, 'r', encoding='utf-8') as file_handle:
        return json.load(file_handle)


def _iter_deck_files(directory: Path):
    if not directory.exists():
        return
    for path in sorted(directory.glob('*.json')):
        if not path.stem.isdigit():
            print(f'  Skipping {path.name}: file name is not a Discord user id')
            continue
        try:
            yield int(path.stem), _load_json(path)
        except (json.JSONDecodeError, OSError) as error:
            print(f'  Skipping unreadable {path.name}: {error}')


async def migrate_standard_decks(connection) -> int:
    migrated = 0
    for user_id, data in _iter_deck_files(DECKS_DIRECTORY):
        for deck_entry in data.get('decks', []):
            await connection.execute(
                '''
                INSERT INTO decks (user_id, name, cards)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, name) DO UPDATE SET cards = EXCLUDED.cards, updated_at = now()
                ''',
                user_id, deck_entry['name'], deck_entry['cards'],
            )
            migrated += 1
    return migrated


async def migrate_tcg_decks(connection) -> int:
    migrated = 0
    for user_id, data in _iter_deck_files(TCG_DECKS_DIRECTORY):
        for deck_entry in data.get('decks', []):
            await connection.execute(
                '''
                INSERT INTO decks_tcg (user_id, name, main_deck, side_deck)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, name) DO UPDATE
                SET main_deck = EXCLUDED.main_deck, side_deck = EXCLUDED.side_deck, updated_at = now()
                ''',
                user_id, deck_entry['name'], deck_entry['deck'], deck_entry['side_deck'],
            )
            migrated += 1
    return migrated


async def migrate_display_names(connection) -> int:
    if not USERNAMES_FILE.exists():
        return 0
    names = _load_json(USERNAMES_FILE)
    migrated = 0
    for user_id_string, entry in names.items():
        if not user_id_string.isdigit():
            print(f'  Skipping username entry with non-numeric key {user_id_string!r}')
            continue
        await connection.execute(
            '''
            INSERT INTO display_names (user_id, name, custom, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (user_id) DO UPDATE
            SET name = EXCLUDED.name, custom = EXCLUDED.custom, updated_at = now()
            ''',
            int(user_id_string), entry['name'], bool(entry.get('custom', False)),
        )
        migrated += 1
    return migrated


async def migrate(database_url: str | None, dry_run: bool) -> None:
    await database.initialize_pool(dsn=database_url)
    try:
        await database.apply_schema()
        async with database.get_pool().acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            try:
                print('Migrating standard decks...')
                deck_count = await migrate_standard_decks(connection)
                print(f'  {deck_count} deck(s)')

                print('Migrating TCG decks...')
                tcg_deck_count = await migrate_tcg_decks(connection)
                print(f'  {tcg_deck_count} TCG deck(s)')

                print('Migrating display names...')
                name_count = await migrate_display_names(connection)
                print(f'  {name_count} display name(s)')

                if dry_run:
                    await transaction.rollback()
                    print('Dry run: transaction rolled back, no rows were written.')
                else:
                    await transaction.commit()
                    print('Migration committed.')
            except BaseException:
                await transaction.rollback()
                raise
        print('Reminder: player profiles are intentionally not migrated (fresh stats start).')
    finally:
        await database.close_pool()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Migrate JSON storage into PostgreSQL.')
    parser.add_argument('--database-url', default=None, help='Overrides DATABASE_URL from the environment.')
    parser.add_argument('--dry-run', action='store_true', help='Run everything inside a transaction and roll it back.')
    arguments = parser.parse_args()
    asyncio.run(migrate(arguments.database_url, arguments.dry_run))


if __name__ == '__main__':
    main()
