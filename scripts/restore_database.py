"""
Restore a pg_dump backup produced by scripts/dump_database.py.

Usage:
    python scripts/restore_database.py <dump file> [--database-url ...]

Drops and recreates the dumped objects in the target database
(--clean --if-exists), so the target ends up exactly matching the backup.
The target database itself must already exist.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

from postgresql_tools import locate_postgresql_binary


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Restore a pg_dump backup of the bot database.')
    parser.add_argument('dump_file', type=Path, help='File produced by dump_database.py.')
    parser.add_argument('--database-url', default=None, help='Overrides DATABASE_URL from the environment.')
    arguments = parser.parse_args()

    if not arguments.dump_file.exists():
        raise SystemExit(f'Dump file not found: {arguments.dump_file}')

    database_url = arguments.database_url or os.environ.get('DATABASE_URL')
    if not database_url:
        raise SystemExit('No database URL: pass --database-url or set DATABASE_URL in .env.')

    pg_restore_path = locate_postgresql_binary('pg_restore')
    command = [
        pg_restore_path,
        '--clean',
        '--if-exists',
        '--no-owner',
        '--dbname', database_url,
        str(arguments.dump_file),
    ]
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print(f'Database restored from {arguments.dump_file}')


if __name__ == '__main__':
    main()
