"""All ppo_transformer hyperparameters as one dataclass tree.

Overridable from `ppo_transformer/.env` (or process environment) via
`load_config()`: `PPO_<SECTION>_<FIELD>=value`. Run-level settings use plain
`PPO_<NAME>` through `env_setting()`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path


@dataclass
class NetConfig:
    embed_dim: int = 512
    num_layers: int = 10
    num_heads: int = 8
    feedforward_dim: int = 2048
    dropout: float = 0.0
    pointer_dim: int = 64
    value_hidden_dim: int = 256
    effect_feature_projection_dim: int = 64
    effect_embedding_dim: int = 32
    identity_embedding_dim: int = 96
    # Identity- and effect-indexed tables are sized to these capacities so
    # new catalog rows slot into reserved space without shape changes (see
    # model_common/migrate_checkpoint.py for growing past them).
    identity_capacity: int = 512
    effect_capacity: int = 320


@dataclass
class TrainConfig:
    vectorized_games: int = 512
    rollout_decisions: int = 65536      # samples collected per iteration
    minibatch_size: int = 1024
    ppo_epochs: int = 3
    clip_range: float = 0.2
    value_clip_range: float = 0.2
    value_loss_weight: float = 0.5
    entropy_bonus_initial: float = 0.01
    entropy_bonus_final: float = 0.001
    entropy_anneal_iterations: int = 400
    gae_lambda: float = 0.95
    discount: float = 1.0               # terminal-only reward
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    gradient_clip: float = 1.0
    checkpoint_interval_iterations: int = 5
    # Opponent mix per game.
    p_latest_vs_latest: float = 0.50
    p_vs_snapshot: float = 0.35
    p_vs_random: float = 0.15
    # Snapshot promotion gate.
    gating_games: int = 200
    gating_win_rate: float = 0.55
    snapshot_capacity: int = 30


@dataclass
class Config:
    net: NetConfig = field(default_factory=NetConfig)
    train: TrainConfig = field(default_factory=TrainConfig)

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_CONFIG = Config()

_ENV_FILE = Path(__file__).resolve().parent / ".env"
_SECTIONS = ("net", "train")


def _load_env_file() -> None:
    if not _ENV_FILE.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
        return
    except ImportError:
        pass
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split("#", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _coerce(raw: str, target_type: type):
    if target_type is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return target_type(raw)


def load_config() -> Config:
    _load_env_file()
    cfg = Config()
    for section_name in _SECTIONS:
        section = getattr(cfg, section_name)
        for f in fields(section):
            raw = os.environ.get(f"PPO_{section_name.upper()}_{f.name.upper()}")
            if raw is not None and raw != "":
                setattr(section, f.name, _coerce(raw, type(getattr(section, f.name))))
    return cfg


def env_setting(name: str, default, target_type: type | None = None):
    _load_env_file()
    raw = os.environ.get(f"PPO_{name.upper()}")
    if raw is None or raw == "":
        return default
    return _coerce(raw, target_type or (type(default) if default is not None else str))
