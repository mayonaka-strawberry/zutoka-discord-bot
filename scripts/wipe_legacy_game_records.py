"""
One-time cutover wipe: remove all legacy (pre-engine_alpha) game records.

Truncates games, game_players, game_decisions, game_events, elo_history,
daily_game_counters, and player_profiles (Elo and statistics reset).
PRESERVED: decks, decks_tcg, display_names, schema_metadata.

Reads DATABASE_URL from the environment or .env, prints per-table row counts,
and executes only with --confirm.

Usage:
    python scripts/wipe_legacy_game_records.py            # preview counts
    python scripts/wipe_legacy_game_records.py --confirm  # execute
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

TABLES_TO_WIPE = (
    'game_decisions',
    'game_events',
    'game_players',
    'games',
    'elo_history',
    'daily_game_counters',
    'player_profiles',
)
TABLES_PRESERVED = ('decks', 'decks_tcg', 'display_names', 'schema_metadata')


async def run(confirm: bool) -> int:
    import asyncpg
    from dotenv import load_dotenv

    load_dotenv(REPOSITORY_ROOT / '.env')
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print('DATABASE_URL is not set (environment or .env).')
        return 2

    connection = await asyncpg.connect(database_url)
    try:
        print(f'Database: {database_url.rsplit("@", 1)[-1]}')
        print('\nRows that would be deleted:')
        for table in TABLES_TO_WIPE:
            count = await connection.fetchval(f'SELECT count(*) FROM {table}')
            print(f'  {table}: {count}')
        print('\nPreserved tables:')
        for table in TABLES_PRESERVED:
            count = await connection.fetchval(f'SELECT count(*) FROM {table}')
            print(f'  {table}: {count}')

        if not confirm:
            print('\nDry run only. Re-run with --confirm to execute the wipe.')
            return 0

        await connection.execute(
            'TRUNCATE ' + ', '.join(TABLES_TO_WIPE) + ' RESTART IDENTITY CASCADE'
        )
        print('\nWipe executed.')
        return 0
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirm', action='store_true',
                        help='actually delete; without it, only counts are shown')
    arguments = parser.parse_args()
    return asyncio.run(run(arguments.confirm))


if __name__ == '__main__':
    raise SystemExit(main())
