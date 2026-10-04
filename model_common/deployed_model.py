"""Checkpoint discovery in the untracked repository-root model/ directory, filled by
hand because checkpoints exceed GitHub's 100 MB file limit. Each stack has one entry
named after its package:

    model/ppo_transformer          a file with no extension
    model/ppo_transformer.pt       the same, with the extension
    model/ppo_transformer/*.pt     a directory: the last file by name wins

Stdlib only, so importing it never needs torch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIRECTORY = REPOSITORY_ROOT / 'model'
CHECKPOINT_SUFFIX = '.pt'


def resolve_deployed_checkpoint(name: str) -> Optional[Path]:
    """The manually deployed checkpoint for `name`, or None when absent."""
    if not MODEL_DIRECTORY.is_dir():
        return None

    entry = MODEL_DIRECTORY / name
    if entry.is_file():
        return entry
    if entry.is_dir():
        checkpoints = sorted(entry.glob(f'*{CHECKPOINT_SUFFIX}'))
        if checkpoints:
            return checkpoints[-1]
        return None

    suffixed = MODEL_DIRECTORY / f'{name}{CHECKPOINT_SUFFIX}'
    if suffixed.is_file():
        return suffixed
    return None
