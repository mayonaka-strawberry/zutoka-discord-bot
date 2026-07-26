"""Bounded checkpoint history.

Checkpoints are large (tens to hundreds of MB each) and a long run writes one
every few iterations, so an unbounded history fills the disk well before the
run finishes. This keeps the newest N and deletes the rest.

Anything still referenced elsewhere is protected: the best checkpoint, and every
league snapshot / opponent-pool entry, since those are reloaded as opponents
during self-play. Deleting one of those would break the run rather than just
lose history.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def prune_checkpoints(directory: str | Path, pattern: str, keep: int,
                      protected: Iterable[str | None] = ()) -> list[Path]:
    """Deletes all but the newest `keep` checkpoints matching `pattern`.

    Relies on zero-padded filenames sorting chronologically, which is how both
    stacks name them (`step_00000123.pt`, `iteration_00042.pt`).

    `keep <= 0` disables pruning. Returns the paths actually removed.
    """
    if keep <= 0:
        return []
    directory = Path(directory)
    if not directory.is_dir():
        return []

    protected_paths = set()
    for entry in protected:
        if entry:
            try:
                protected_paths.add(Path(entry).resolve())
            except OSError:
                continue

    checkpoints = sorted(directory.glob(pattern))
    removed: list[Path] = []
    for path in checkpoints[:-keep]:
        try:
            if path.resolve() in protected_paths:
                continue
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed
