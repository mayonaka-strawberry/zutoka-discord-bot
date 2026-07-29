"""Append-only per-iteration metrics, one JSON object per line.

JSONL rather than tensorboard: no dependency, readable with the standard
library, and trivially diffable between runs. Both training stacks print a
single loss figure per iteration and keep nothing, which makes a finished run
impossible to review after the fact.
"""

from __future__ import annotations

import json
from pathlib import Path


def append_metrics(path: str | Path, iteration: int, scalars: dict) -> None:
    """Appends one record to `path`, creating the file if needed.

    Never raises: losing a metrics line must not take down a run that is hours
    or days in. Values that do not survive a JSON round trip are stringified
    rather than dropped, so a bad entry is visible instead of silently missing.
    """
    record = {'iteration': iteration}
    for name, value in scalars.items():
        record[name] = value if isinstance(value, (int, float, bool, str)) else str(value)
    try:
        with Path(path).open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record) + '\n')
    except OSError as error:
        print(f'warning: could not append metrics to {path}: {error}')


def read_metrics(path: str | Path) -> list[dict]:
    """Every record in `path`, skipping any line that fails to parse."""
    records = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
