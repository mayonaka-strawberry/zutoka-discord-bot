"""
Binary backup of the bot database using pg_dump (custom format).

Usage:
    python scripts/dump_database.py [--database-url ...] [--output path]

Restore with scripts/restore_database.py. For portable, human-readable
transfers between machines or PostgreSQL versions, use
scripts/export_database.py instead.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

from postgresql_tools import locate_postgresql_binary


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Back up the bot database with pg_dump.')
    parser.add_argument('--database-url', default=None, help='Overrides DATABASE_URL from the environment.')
    parser.add_argument('--output', type=Path, default=None, help='Output dump file path.')
    arguments = parser.parse_args()

    database_url = arguments.database_url or os.environ.get('DATABASE_URL')
    if not database_url:
        raise SystemExit('No database URL: pass --database-url or set DATABASE_URL in .env.')

    output_path = arguments.output
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        output_path = Path(f'zutoka-{timestamp}.dump')

    pg_dump_path = locate_postgresql_binary('pg_dump')
    command = [
        pg_dump_path,
        '--format=custom',
        '--file', str(output_path),
        database_url,
    ]
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print(f'Database dumped to {output_path}')


if __name__ == '__main__':
    main()
