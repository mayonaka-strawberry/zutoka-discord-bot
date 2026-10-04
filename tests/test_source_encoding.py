"""Guard against text files saved in the wrong encoding: a UTF-8 byte order mark, or
mojibake from a Windows ANSI round trip. Mojibake is still valid UTF-8, so only a
content match catches it (it once mangled the bot name 'メカうにぐり').

The pattern uses \\u escapes and no damaged text appears in this file, so the guard
cannot match itself.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# .gz covers the golden match baseline (decompressed first). Files under .idea/,
# .env and .gitignore are left out by suffix.
SCANNED_SUFFIXES = {'.py', '.md', '.json', '.jsonl', '.sql', '.txt', '.gz'}
COMPRESSED_SUFFIXES = {'.gz'}
SKIPPED_DIRECTORY_NAMES = {
    '.git',
    '.venv',
    'venv',
    'node_modules',
    '__pycache__',
    'site-packages',
    '.pytest_cache',
    '.mypy_cache',
}

BYTE_ORDER_MARK = b'\xef\xbb\xbf'

# A UTF-8 lead byte rendered as cp1252, followed by a continuation byte rendered
# as cp1252. Lead bytes c2-f4 map to themselves in the Latin-1 region. The
# continuation bytes 80-bf map to themselves in a0-bf, and to the assorted
# punctuation cp1252 places in 80-9f.
MOJIBAKE_PATTERN = re.compile(
    '[Â-ô]'
    '[-¿ŒœŠšŸŽžƒˆ˜'
    '–—‘-„†-•…‰‹›€™]'
)


def _scanned_files() -> list[Path]:
    """Every text file worth checking; .venv and .git are never walked."""
    found: list[Path] = []
    pending = [REPOSITORY_ROOT]
    while pending:
        directory = pending.pop()
        try:
            entries = list(directory.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIPPED_DIRECTORY_NAMES:
                    pending.append(entry)
            elif entry.suffix.lower() in SCANNED_SUFFIXES:
                found.append(entry)
    return found


def _relative(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def _file_contents(path: Path) -> tuple[str, bytes | None, str | None]:
    """(label, raw bytes, error) for one file, decompressing .gz. On failure the bytes
    are None and `error` explains, so a damaged archive is a finding, not a crash."""
    label = _relative(path)
    try:
        raw = path.read_bytes()
    except OSError as exception:
        return label, None, f'could not be read: {exception}'

    if path.suffix.lower() in COMPRESSED_SUFFIXES:
        try:
            return f'{label} (decompressed)', gzip.decompress(raw), None
        except (OSError, EOFError) as exception:
            return label, None, f'could not be decompressed: {exception}'

    return label, raw, None


def test_no_source_file_starts_with_a_byte_order_mark():
    offenders = []
    for path in _scanned_files():
        # Unreadable files are reported by the mojibake test; skipping them here
        # keeps a single failure from being reported twice.
        label, raw, error = _file_contents(path)
        if raw is None:
            continue
        if raw.startswith(BYTE_ORDER_MARK):
            offenders.append(label)

    assert not offenders, (
        'These files begin with a UTF-8 byte order mark, which usually means an '
        'editor or shell saved them as "UTF-8 with BOM" after reading them as '
        'Windows ANSI. Re-save each one as UTF-8 without a BOM:\n  '
        + '\n  '.join(sorted(offenders))
    )


def test_no_source_file_contains_mojibake():
    offenders = []
    for path in _scanned_files():
        label, raw, error = _file_contents(path)
        if error is not None:
            offenders.append(f'{label}: {error}')
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            offenders.append(f'{label}: not valid UTF-8')
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            damaged = {match.group(0) for match in MOJIBAKE_PATTERN.finditer(line)}
            if damaged:
                offenders.append(
                    f'{label}:{line_number}: {" ".join(sorted(damaged))}'
                )

    assert not offenders, (
        'These lines contain UTF-8 text that was decoded as Windows ANSI and '
        're-encoded, so the file now holds the wrong characters. Restore the '
        'original text and re-save as UTF-8:\n  '
        + '\n  '.join(offenders)
    )
