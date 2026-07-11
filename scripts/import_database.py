"""
Import a JSON export produced by scripts/export_database.py.

Usage:
    python scripts/import_database.py <export file> [--database-url ...] [--replace] [--dry-run]

By default rows are upserted (idempotent: importing over existing data
updates rather than duplicates). With --replace, all data tables are
truncated first so the database ends up exactly matching the export file.
--dry-run rolls the whole transaction back after printing counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / 'scripts'))

from dotenv import load_dotenv

from database_transfer import TABLE_SPECIFICATIONS, build_upsert_sql, deserialize_row
from zutomayo.data import database

# Children before parents, for TRUNCATE ... CASCADE safety and clarity.
REPLACE_TRUNCATION_ORDER = (
    'elo_history', 'game_events', 'game_decisions', 'game_players', 'games',
    'daily_game_counters', 'decks_tcg', 'decks', 'display_names',
    'player_profiles', 'schema_metadata',
)


def _load_export_file(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as export_file:
        payload = json.load(export_file)
    if 'tables' not in payload:
        raise ValueError(f'{path} does not look like a zutoka database export.')
    return payload


async def import_export_file(
    export_path: Path,
    database_url: str | None,
    *,
    replace: bool,
    dry_run: bool,
) -> None:
    payload = _load_export_file(export_path)

    await database.initialize_pool(dsn=database_url)
    try:
        await database.apply_schema()
        async with database.get_pool().acquire() as connection:
            local_schema_version = await connection.fetchval(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'",
            )
            file_schema_version = int(payload.get('schema_version') or 1)
            if file_schema_version > int(local_schema_version or 1):
                raise ValueError(
                    f'Export file has schema version {file_schema_version}, newer than '
                    f'this installation ({local_schema_version}). Update the bot first.'
                )

            transaction = connection.transaction()
            await transaction.start()
            try:
                if replace:
                    await connection.execute(
                        'TRUNCATE TABLE ' + ', '.join(REPLACE_TRUNCATION_ORDER) + ' CASCADE'
                    )
                    print('Existing data truncated (--replace).')

                total_rows = 0
                for specification in TABLE_SPECIFICATIONS:
                    rows = payload['tables'].get(specification.name, [])
                    if rows:
                        await connection.executemany(
                            build_upsert_sql(specification),
                            [deserialize_row(specification, row) for row in rows],
                        )
                    total_rows += len(rows)
                    print(f'  {specification.name}: {len(rows)} row(s)')

                if dry_run:
                    await transaction.rollback()
                    print(f'Dry run: {total_rows} row(s) processed, transaction rolled back.')
                else:
                    await transaction.commit()
                    print(f'Imported {total_rows} row(s) from {export_path}')
            except BaseException:
                try:
                    await transaction.rollback()
                except Exception:
                    pass  # already resolved by a failed commit
                raise
    finally:
        await database.close_pool()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Import a JSON database export.')
    parser.add_argument('export_file', type=Path, help='File produced by export_database.py.')
    parser.add_argument('--database-url', default=None, help='Overrides DATABASE_URL from the environment.')
    parser.add_argument('--replace', action='store_true',
                        help='Truncate all data tables first so the database exactly matches the export.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run everything inside a transaction and roll it back.')
    arguments = parser.parse_args()
    asyncio.run(import_export_file(
        arguments.export_file, arguments.database_url,
        replace=arguments.replace, dry_run=arguments.dry_run,
    ))


if __name__ == '__main__':
    main()
