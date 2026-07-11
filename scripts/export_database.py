"""
Export the entire bot database to one portable JSON file.

Usage:
    python scripts/export_database.py [--database-url ...] [--output path]

Writes every table (profiles, display names, decks, game records, decision
logs, events, elo history) to a single JSON file that
scripts/import_database.py can restore on any machine and PostgreSQL version.
For routine compact binary backups use scripts/dump_database.py instead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / 'scripts'))

from dotenv import load_dotenv

from database_transfer import TABLE_SPECIFICATIONS, serialize_row
from zutomayo.data import database

EXPORT_FORMAT_VERSION = 1


async def export(database_url: str | None, output_path: Path) -> None:
    await database.initialize_pool(dsn=database_url)
    try:
        tables: dict[str, list[dict]] = {}
        async with database.get_pool().acquire() as connection:
            schema_version = await connection.fetchval(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'",
            )
            for specification in TABLE_SPECIFICATIONS:
                order_by = ', '.join(specification.primary_key)
                rows = await connection.fetch(
                    f'SELECT {", ".join(specification.columns)} '
                    f'FROM {specification.name} ORDER BY {order_by}',
                )
                tables[specification.name] = [serialize_row(specification, row) for row in rows]
                print(f'  {specification.name}: {len(rows)} row(s)')

        export_payload = {
            'export_format_version': EXPORT_FORMAT_VERSION,
            'schema_version': schema_version or '1',
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'tables': tables,
        }
        with open(output_path, 'w', encoding='utf-8') as output_file:
            json.dump(export_payload, output_file, ensure_ascii=False)
        total_rows = sum(len(rows) for rows in tables.values())
        print(f'Exported {total_rows} row(s) to {output_path}')
    finally:
        await database.close_pool()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Export the bot database to a JSON file.')
    parser.add_argument('--database-url', default=None, help='Overrides DATABASE_URL from the environment.')
    parser.add_argument('--output', type=Path, default=None, help='Output file path.')
    arguments = parser.parse_args()

    output_path = arguments.output
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        output_path = Path(f'zutoka-export-{timestamp}.json')

    asyncio.run(export(arguments.database_url, output_path))


if __name__ == '__main__':
    main()
