"""All alpha_zero hyperparameters as one dataclass tree.

Every tunable lives here so runs are reproducible from a single config dump.
The defaults in this file are the tracked baseline; `alpha_zero/.env` (or the
process environment) layers per-machine overrides on top via `load_config()`:

    ALPHA_<SECTION>_<FIELD>=value    e.g. ALPHA_MCTS_SIMULATIONS_IN_GAME=256
    ALPHA_<NAME>=value               run-level, read by the entry scripts

To see every key with its current default:

    python -m alpha_zero.config

Redirecting that to `alpha_zero/.env` writes a fully-commented file that
changes nothing until you uncomment a line.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

from engine_alpha.config import EngineConfig
from model_common import env_config

PREFIX = "ALPHA"
ENV_FILE = Path(__file__).resolve().parent / ".env"
SECTIONS = ("engine", "net", "mcts", "train", "league")


@dataclass
class NetConfig:
    # Self-play throughput, not capacity, is the binding constraint at 256
    # simulations per decision, so the net is sized for cheap forwards. 8
    # layers x 384 is ~14M parameters; 8 x 512 (~25M) is the next step up if
    # games/hour turns out to be comfortable.
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
    # Identity-indexed tables are sized to this capacity rather than the
    # current card count, so new cards occupy pre-allocated rows and existing
    # checkpoints keep loading (see model_common/migrate_checkpoint.py for
    # growing past it).
    identity_capacity: int = 512
    # Same headroom rule for the effect table (one row per effect program).
    effect_capacity: int = 320


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
    # Counts in-game *decisions*, not turns — micro-decision chains (mulligan
    # marks, set A/B, effect ordering) mean one turn is several decisions.
    temperature_moves: int = 30
    playout_cap_fraction: float = 0.25  # fraction of games played at reduced budget
    playout_cap_divisor: int = 4
    # Break near-ties among root visit counts with prior-plus-Gumbel scores
    # instead of index order; helps when the budget is too small for visits to
    # discriminate.
    use_gumbel_root: bool = False


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
    # Weight on the win/draw/loss value cross-entropy relative to the policy
    # cross-entropy.
    value_loss_weight: float = 1.0
    checkpoint_interval_steps: int = 2000
    # Checkpoints kept on disk, newest first. The best checkpoint and every
    # league snapshot are always retained regardless. 0 disables pruning.
    checkpoint_retention: int = 5
    evaluator_max_batch: int = 512
    evaluator_max_wait_ms: float = 2.0
    # Exponential moving average of weights, used for published/evaluation
    # weights; 0 disables.
    ema_decay: float = 0.999
    # Recompute encoder activations in the backward pass to trade compute
    # for memory (per-layer activation checkpointing).
    gradient_checkpointing: bool = True
    # Master seed for self-play matchups, deck draws and batch sampling.
    seed: int = 1990929


@dataclass
class LeagueConfig:
    hall_of_fame_cap: int = 30
    protected_newest: int = 3
    decks_per_snapshot: int = 8
    deck_min_games: int = 30
    gating_games: int = 200
    gating_win_rate: float = 0.55
    # Search budget both sides get during gating. Set this to
    # ALPHA_LIVE_SIMULATIONS to make promotion measure deployed strength.
    gating_simulations: int = 128
    elo_k_factor: float = 24.0
    # Beta prior pulling deck win rates toward 0.5: (wins + p) / (games + 2p).
    deck_shrinkage_prior: float = 5.0
    # Per-game opponent sampling distribution (must sum to 1.0).
    p_latest_vs_latest: float = 0.55
    p_vs_snapshot_stored_deck: float = 0.20
    p_vs_snapshot_drafting: float = 0.10
    # Implicit remainder in sample_matchup; validated to stay consistent.
    p_pool_decks: float = 0.15
    # Deck source for the fixed-deck matchups. The pool is the exported set of
    # real player decks (scripts/export_training_decks.py); the remainder are
    # generated random legal decks. Drafted games are unaffected — deck
    # building there is the model's own job. An empty path means
    # model_common.deck_pool.DEFAULT_DECK_POOL_PATH; a missing file falls back
    # to random decks.
    deck_pool_path: str = ''
    probability_user_deck: float = 0.75


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    net: NetConfig = field(default_factory=NetConfig)
    mcts: MCTSConfig = field(default_factory=MCTSConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    league: LeagueConfig = field(default_factory=LeagueConfig)

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> "Config":
        """Fails loudly on inconsistent settings.

        Once `.env` is the primary surface, a typo'd override is far more
        likely than a bad default, and most of these would otherwise surface
        as a confusing shape error thousands of steps into a run.
        """
        env_config.check_probabilities_sum(
            {"p_latest_vs_latest": self.league.p_latest_vs_latest,
             "p_vs_snapshot_stored_deck": self.league.p_vs_snapshot_stored_deck,
             "p_vs_snapshot_drafting": self.league.p_vs_snapshot_drafting,
             "p_pool_decks": self.league.p_pool_decks},
            "ALPHA_LEAGUE opponent sampling")

        if not 0.0 <= self.league.probability_user_deck <= 1.0:
            raise ValueError(
                "ALPHA_LEAGUE_PROBABILITY_USER_DECK must be within [0, 1], got "
                f"{self.league.probability_user_deck}")
        if not 0.0 <= self.mcts.playout_cap_fraction <= 1.0:
            raise ValueError(
                "ALPHA_MCTS_PLAYOUT_CAP_FRACTION must be within [0, 1], got "
                f"{self.mcts.playout_cap_fraction}")
        if not 0.0 <= self.train.ema_decay < 1.0:
            raise ValueError(
                f"ALPHA_TRAIN_EMA_DECAY must be within [0, 1), got {self.train.ema_decay}")

        if self.net.embed_dim % self.net.num_heads:
            raise ValueError(
                f"ALPHA_NET_EMBED_DIM ({self.net.embed_dim}) must be divisible by "
                f"ALPHA_NET_NUM_HEADS ({self.net.num_heads})")
        if self.train.warmup_steps >= self.train.learning_rate_decay_steps:
            raise ValueError(
                f"ALPHA_TRAIN_WARMUP_STEPS ({self.train.warmup_steps}) must be below "
                f"ALPHA_TRAIN_LEARNING_RATE_DECAY_STEPS "
                f"({self.train.learning_rate_decay_steps})")

        # Imported lazily: reading the card database is not worth paying for on
        # every config load, and a checkout without card data should still be
        # able to render the template.
        try:
            from engine_alpha.cards import NUM_CARDS, NUM_EFFECTS
        except Exception:
            return self
        if self.net.identity_capacity < NUM_CARDS:
            raise ValueError(
                f"ALPHA_NET_IDENTITY_CAPACITY ({self.net.identity_capacity}) is below "
                f"the card count ({NUM_CARDS}); see model_common/migrate_checkpoint.py")
        if self.net.effect_capacity < NUM_EFFECTS:
            raise ValueError(
                f"ALPHA_NET_EFFECT_CAPACITY ({self.net.effect_capacity}) is below "
                f"the effect count ({NUM_EFFECTS}); see model_common/migrate_checkpoint.py")
        return self


DEFAULT_CONFIG = Config()

# Run-level settings, mirrored by the flags in scripts/run_train.py. Kept here
# so the generated template documents them alongside the section fields.
RUN_SETTINGS: tuple[tuple[str, object, str], ...] = (
    ("runs_dir", "alpha_zero/runs", "where checkpoints, buffer and league live"),
    ("workers", 0, "0 = inline self-play on the training device"),
    ("iterations", 100, ""),
    ("games_per_iter", 32, ""),
    ("train_steps", 200, "optimizer steps per iteration (reuse-throttled)"),
    ("gate_every", 5, "gating/promotion every N iterations"),
    ("gating_games", None, "blank = use ALPHA_LEAGUE_GATING_GAMES"),
    ("device", None, "blank = cuda when available, else cpu"),
    ("live_mode", "search", "deployed agent: search or policy"),
    ("live_simulations", 64, "deployed search budget per decision"),
)

_SECTION_NOTES = {
    "engine": "NOTE: recorded for run provenance only. The engine uses inline\n"
              "constants, so changing these does NOT alter engine behaviour.",
}


def load_config() -> Config:
    """Config with alpha_zero/.env + environment overrides applied."""
    config = env_config.apply_env_overrides(Config(), PREFIX, SECTIONS, ENV_FILE)
    return config.validate()


def env_setting(name: str, default, target_type: type | None = None):
    """Run-level setting: ALPHA_<NAME> from .env/environment, else default.
    Used by entry scripts for workers/iterations/device/etc."""
    return env_config.env_setting(name, default, PREFIX, ENV_FILE, target_type)


def format_template() -> str:
    """The full override surface as a `.env` body (every line commented)."""
    return env_config.format_env_template(
        Config(), PREFIX, SECTIONS, "alpha_zero training parameters",
        "alpha_zero.config", run_settings=RUN_SETTINGS,
        section_notes=_SECTION_NOTES)


if __name__ == "__main__":
    print(format_template())
