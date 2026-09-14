"""Guard against source files being saved in the wrong text encoding.

A file that has been round-tripped through Windows ANSI (read as cp1252, written
back out as UTF-8) is still *valid* UTF-8 afterwards - it simply encodes the wrong
characters. No encoding-level check can catch that, and neither can git, so this
test looks for the resulting character sequences directly.

That is exactly how the bot name 'メカうにぐり' was mangled in the solo-game
acceptance message: the UTF-8 bytes e3 83 a1 of its first character were read as
three separate cp1252 characters and then re-encoded as three characters. The same
round-trip also left a UTF-8 byte order mark behind, which breaks ast.parse() and
therefore silently excludes the file from any AST-based tooling.

The regular expression below is written with \\u escapes on purpose, and no damaged
text is quoted anywhere in this file: both keep the source pure ASCII apart from
the one correct name above, so the guard can never match itself.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# '.gz' covers the golden match baseline, which carries narration text with player
# names in it; it is decompressed before being scanned. The IDE files under
# .idea/, along with .env and .gitignore, are deliberately left out as noise.
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
    """Every text file in the repository worth checking.

    Directories are filtered as the walk proceeds so the virtual environment and
    the git object store are never descended into.
    """
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
    """Raw bytes for one file, transparently decompressing a compressed one.

    Returns (label, raw_bytes, error). The label names the decompressed form so a
    reported line number is not mistaken for an offset into the archive. A file
    that cannot be read or decompressed comes back with raw_bytes None and an
    error describing it, so a damaged archive is reported as a finding rather
    than erroring the whole test out.
    """
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
