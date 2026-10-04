"""alpha_zero hyperparameters as one dataclass tree; the defaults are the tracked
baseline. alpha_zero/.env or the environment overrides them through load_config():

    ALPHA_<SECTION>_<FIELD>=value    e.g. ALPHA_MCTS_SIMULATIONS_IN_GAME=256
    ALPHA_<NAME>=value               run-level, read by the entry scripts

`python -m alpha_zero.config` prints every key, commented out.
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
    # Sized for fast self-play forwards: 8 x 384 is about 15M parameters.
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
    # Rows beyond the card count, so new cards keep checkpoints loadable
    # (model_common/migrate_checkpoint.py grows past it).
    identity_capacity: int = 512
    # Same headroom, for the effect table.
    effect_capacity: int = 320
    # Same headroom, for the song table.
    song_capacity: int = 64


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
    # Counts decisions, not turns (a turn is several decisions).
    temperature_moves: int = 30
    playout_cap_fraction: float = 0.25  # fraction of games played at reduced budget
    playout_cap_divisor: int = 4
    # Break near-ties in root visits by prior plus Gumbel noise, not index order.
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
    # Value cross-entropy weight relative to the policy loss.
    value_loss_weight: float = 1.0
    # CHAOS self-defeat value-loss weights: the self-defeater's decisions train
    # harder, the gifted winner's softer (model_common.termination).
    self_defeat_loss_sample_weight: float = 4.0
    self_defeat_win_sample_weight: float = 0.25
    checkpoint_interval_steps: int = 2000
    # Newest checkpoints kept; the best one and league snapshots always stay. 0 keeps all.
    checkpoint_retention: int = 5
    evaluator_max_batch: int = 512
    evaluator_max_wait_ms: float = 2.0
    # Weight EMA for published and evaluation weights; 0 disables.
    ema_decay: float = 0.999
    # Recompute activations in the backward pass: less memory, more compute.
    gradient_checkpointing: bool = True
    # Master seed for self-play matchups, deck draws and batch sampling.
    seed: int = 19990929


@dataclass
class LeagueConfig:
    hall_of_fame_cap: int = 30
    protected_newest: int = 3
    decks_per_snapshot: int = 8
    deck_min_games: int = 30
    gating_games: int = 200
    gating_win_rate: float = 0.55
    # Gating search budget; set to ALPHA_LIVE_SIMULATIONS to gate on deployed strength.
    gating_simulations: int = 128
    elo_k_factor: float = 24.0
    # Beta prior pulling deck win rates toward 0.5: (wins + p) / (games + 2p).
    deck_shrinkage_prior: float = 5.0
    # Per-game opponent sampling distribution (must sum to 1.0).
    p_latest_vs_latest: float = 0.55
    p_vs_snapshot_stored_deck: float = 0.20
    p_vs_snapshot_drafting: float = 0.10
    # The remainder in sample_matchup; validated with the others.
    p_pool_decks: float = 0.15
    # Real player decks for fixed-deck matchups (scripts/export_training_decks.py);
    # the rest are generated, and a missing file means all are. Keeps CHAOS decks:
    # only p_pool_decks, the stored-deck learner seat and fixed-deck gating read it.
    deck_pool_path: str = 'data/training_decks_alpha_zero.json'
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
        """Fail at load on inconsistent settings, not with a shape error mid-run."""
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
        if not self.league.deck_pool_path:
            # An empty path is the working directory, which fails deep in the loader.
            raise ValueError(
                "ALPHA_LEAGUE_DECK_POOL_PATH must name a pool file; an empty "
                "value reads as unset and resolves to the working directory")
        if not 0.0 <= self.mcts.playout_cap_fraction <= 1.0:
            raise ValueError(
                "ALPHA_MCTS_PLAYOUT_CAP_FRACTION must be within [0, 1], got "
                f"{self.mcts.playout_cap_fraction}")
        if not 0.0 <= self.train.ema_decay < 1.0:
            raise ValueError(
                f"ALPHA_TRAIN_EMA_DECAY must be within [0, 1), got {self.train.ema_decay}")
        if self.train.self_defeat_loss_sample_weight < 1.0:
            raise ValueError(
                "ALPHA_TRAIN_SELF_DEFEAT_LOSS_SAMPLE_WEIGHT must be at least 1.0 "
                "so a self-defeat is never trained more weakly than a normal "
                f"loss, got {self.train.self_defeat_loss_sample_weight}")
        if not 0.0 < self.train.self_defeat_win_sample_weight <= 1.0:
            raise ValueError(
                "ALPHA_TRAIN_SELF_DEFEAT_WIN_SAMPLE_WEIGHT must be within (0, 1], "
                f"got {self.train.self_defeat_win_sample_weight}")

        if self.net.embed_dim % self.net.num_heads:
            raise ValueError(
                f"ALPHA_NET_EMBED_DIM ({self.net.embed_dim}) must be divisible by "
                f"ALPHA_NET_NUM_HEADS ({self.net.num_heads})")
        if self.train.warmup_steps >= self.train.learning_rate_decay_steps:
            raise ValueError(
                f"ALPHA_TRAIN_WARMUP_STEPS ({self.train.warmup_steps}) must be below "
                f"ALPHA_TRAIN_LEARNING_RATE_DECAY_STEPS "
                f"({self.train.learning_rate_decay_steps})")

        # Lazy: the template must render without card data.
        try:
            from engine_alpha.cards import NUM_CARDS, NUM_EFFECTS, NUM_SONGS
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
        if self.net.song_capacity < NUM_SONGS:
            raise ValueError(
                f"ALPHA_NET_SONG_CAPACITY ({self.net.song_capacity}) is below "
                f"the song count ({NUM_SONGS}); see model_common/migrate_checkpoint.py")
        return self


DEFAULT_CONFIG = Config()

# Run-level settings (the scripts/run_train.py flags, plus the live agent's mode and
# budget read by inference.py), listed in the template.
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
    """Run-level setting ALPHA_<NAME> from .env or the environment, else `default`."""
    return env_config.env_setting(name, default, PREFIX, ENV_FILE, target_type)


def format_template() -> str:
    """The full override surface as a `.env` body (every line commented)."""
    return env_config.format_env_template(
        Config(), PREFIX, SECTIONS, "alpha_zero training parameters",
        "alpha_zero.config", run_settings=RUN_SETTINGS,
        section_notes=_SECTION_NOTES)


if __name__ == "__main__":
    print(format_template())
