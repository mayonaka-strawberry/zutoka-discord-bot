"""All engine_alpha hyperparameters as one dataclass tree.

Every tunable lives here so runs are reproducible from a single config dump.
Values can be overridden from `engine_alpha/.env` (or process environment)
via `load_config()`: `ALPHA_<SECTION>_<FIELD>=value`, e.g.
`ALPHA_MCTS_SIMULATIONS_IN_GAME=256`, `ALPHA_TRAIN_BATCH_SIZE=1024`.
Run-level settings (workers, iterations, ...) use plain `ALPHA_<NAME>` and
are read by the entry scripts through `env_setting()`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path


@dataclass
class EngineConfig:
    deck_size: int = 20
    max_copies: int = 2
    starting_hp: int = 100
    opening_hand_size: int = 5
    chronos_size: int = 18
    midnight: int = 4
    night_end: int = 8
    noon: int = 13
    max_turns: int = 200  # hard safety cap; a legal game always deck-outs long before this


@dataclass
class NetConfig:
    embed_dim: int = 384
    num_layers: int = 8
    num_heads: int = 8
    feedforward_dim: int = 1536
    dropout: float = 0.0
    pointer_dim: int = 64
    value_hidden_dim: int = 256
    effect_feature_projection_dim: int = 64
    effect_embedding_dim: int = 32
    identity_embedding_dim: int = 96


@dataclass
class MCTSConfig:
    simulations_in_game: int = 256
    simulations_draft: int = 128
    simulations_small: int = 64  # used when |legal| <= small_decision_threshold
    small_decision_threshold: int = 3
    c_puct_init: float = 1.25
    c_puct_base: float = 19652.0
    dirichlet_epsilon: float = 0.25
    dirichlet_alpha_scale: float = 10.0  # alpha = scale / |legal|, clipped below
    dirichlet_alpha_min: float = 0.03
    dirichlet_alpha_max: float = 1.0
    virtual_loss: int = 3
    max_leaves_per_step: int = 12
    temperature_moves: int = 12  # in-game decisions after mulligan at tau=1.0, then argmax
    playout_cap_fraction: float = 0.25  # fraction of games played at reduced budget
    playout_cap_divisor: int = 4


@dataclass
class TrainConfig:
    replay_capacity: int = 1_500_000
    shard_size: int = 4096
    batch_size: int = 1024
    learning_rate: float = 3e-4
    learning_rate_final: float = 3e-5
    learning_rate_decay_steps: int = 150_000  # cosine horizon; match planned total optimizer steps
    weight_decay: float = 1e-4
    adam_beta1: float = 0.9
    adam_beta2: float = 0.95
    warmup_steps: int = 2000
    gradient_clip: float = 1.0
    max_sample_reuse: float = 8.0
    checkpoint_interval_steps: int = 2000
    baseline_eval_interval_steps: int = 5000
    baseline_eval_games: int = 100
    evaluator_max_batch: int = 512
    evaluator_max_wait_ms: float = 2.0


@dataclass
class LeagueConfig:
    hall_of_fame_cap: int = 30
    protected_newest: int = 3
    decks_per_snapshot: int = 8
    deck_min_games: int = 30
    gating_games: int = 200
    gating_win_rate: float = 0.55
    # Per-game opponent sampling distribution (must sum to 1.0).
    p_latest_vs_latest: float = 0.55
    p_vs_snapshot_stored_deck: float = 0.20
    p_vs_snapshot_drafting: float = 0.10
    p_random_decks: float = 0.15


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    net: NetConfig = field(default_factory=NetConfig)
    mcts: MCTSConfig = field(default_factory=MCTSConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    league: LeagueConfig = field(default_factory=LeagueConfig)

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_CONFIG = Config()

_ENV_FILE = Path(__file__).resolve().parent / ".env"
_SECTIONS = ("engine", "net", "mcts", "train", "league")


def _load_env_file() -> None:
    """Loads engine_alpha/.env into os.environ (existing vars win).

    Uses python-dotenv when available; otherwise a minimal KEY=VALUE parser
    ('#' comments and blank lines skipped) so dotenv is not a hard dependency.
    """
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
    """Config with engine_alpha/.env + environment overrides applied."""
    _load_env_file()
    cfg = Config()
    for section_name in _SECTIONS:
        section = getattr(cfg, section_name)
        for f in fields(section):
            env_key = f"ALPHA_{section_name.upper()}_{f.name.upper()}"
            raw = os.environ.get(env_key)
            if raw is not None and raw != "":
                setattr(section, f.name, _coerce(raw, type(getattr(section, f.name))))
    return cfg


def env_setting(name: str, default, target_type: type | None = None):
    """Run-level setting: ALPHA_<NAME> from .env/environment, else default.
    Used by entry scripts for workers/iterations/device/etc."""
    _load_env_file()
    raw = os.environ.get(f"ALPHA_{name.upper()}")
    if raw is None or raw == "":
        return default
    return _coerce(raw, target_type or (type(default) if default is not None else str))
