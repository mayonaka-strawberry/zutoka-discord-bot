"""
Locate PostgreSQL client binaries (pg_dump, pg_restore) across platforms.

Search order:
1. The PGBIN environment variable (a directory containing the binaries).
2. The system PATH.
3. Platform-default install locations, newest version first:
   - Windows: C:\\Program Files\\PostgreSQL\\<version>\\bin
   - macOS (Homebrew): /opt/homebrew/opt/postgresql@<version>/bin and
     /usr/local/opt/postgresql@<version>/bin
   - Linux (Debian/Ubuntu): /usr/lib/postgresql/<version>/bin
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _version_sort_key(path: Path) -> tuple:
    """Sort version-named directories numerically where possible."""
    name = path.name.split('@')[-1]
    parts = []
    for piece in name.split('.'):
        parts.append(int(piece) if piece.isdigit() else -1)
    return tuple(parts)


def _platform_default_directories() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform == 'win32':
        for root in (Path(r'C:\Program Files\PostgreSQL'), Path(r'C:\Program Files (x86)\PostgreSQL')):
            if root.exists():
                candidates.extend(root.iterdir())
    elif sys.platform == 'darwin':
        for root in (Path('/opt/homebrew/opt'), Path('/usr/local/opt')):
            if root.exists():
                candidates.extend(
                    path for path in root.iterdir() if path.name.startswith('postgresql')
                )
    else:
        root = Path('/usr/lib/postgresql')
        if root.exists():
            candidates.extend(root.iterdir())
    versioned = [path for path in candidates if path.is_dir()]
    versioned.sort(key=_version_sort_key, reverse=True)
    return [path / 'bin' for path in versioned]


def locate_postgresql_binary(binary_name: str) -> str:
    """Full path to a PostgreSQL client binary. Raises FileNotFoundError with
    a PGBIN hint when nothing is found."""
    executable_name = f'{binary_name}.exe' if sys.platform == 'win32' else binary_name

    pgbin_directory = os.environ.get('PGBIN')
    if pgbin_directory:
        candidate = Path(pgbin_directory) / executable_name
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(
            f'{executable_name} not found in PGBIN directory {pgbin_directory}.'
        )

    on_path = shutil.which(binary_name)
    if on_path:
        return on_path

    for bin_directory in _platform_default_directories():
        candidate = bin_directory / executable_name
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        f'Could not find {executable_name}. Install the PostgreSQL client tools, '
        'add their bin directory to PATH, or set the PGBIN environment variable '
        'to the directory containing them (see docs/postgresql_setup.md).'
    )
