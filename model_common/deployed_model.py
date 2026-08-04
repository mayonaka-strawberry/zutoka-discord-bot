"""Checkpoint discovery from the repository-root `model/` directory.

`model/` is the deployment drop point: an untracked folder the operator fills
by hand, so the weights never pass through git (a bare PPO state dict is ~129
MB, well over GitHub's per-file limit). Each stack claims one entry named after
its package - `model/ppo_transformer`, `model/alpha_zero` - and the layout is
deliberately permissive, because the file is placed manually and an extension
is easy to forget:

    model/ppo_transformer          a file, whatever its name says
    model/ppo_transformer.pt       the same file with the usual extension
    model/ppo_transformer/*.pt     a directory, newest checkpoint wins

Stdlib only, and no torch - the live Discord bot imports this path through
`alpha_zero.inference` and `ppo_transformer.inference` on machines that carry
no training code.
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
