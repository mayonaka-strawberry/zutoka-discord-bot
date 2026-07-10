"""
Apply the bot's PostgreSQL schema manually.

Usage:
    python scripts/apply_schema.py [--database-url postgresql://...]

Without --database-url, DATABASE_URL from the environment / .env is used.
The schema is idempotent; running this repeatedly is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from zutomayo.data import database


async def apply(database_url: str | None) -> None:
    await database.initialize_pool(dsn=database_url)
    try:
        await database.apply_schema()
        print('Schema applied successfully.')
    finally:
        await database.close_pool()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Apply the bot database schema.')
    parser.add_argument('--database-url', default=None, help='Overrides DATABASE_URL from the environment.')
    arguments = parser.parse_args()
    asyncio.run(apply(arguments.database_url))


if __name__ == '__main__':
    main()
