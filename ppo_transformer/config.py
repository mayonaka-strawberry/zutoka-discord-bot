"""ppo_transformer hyperparameters as one dataclass tree. ppo_transformer/.env sets
every key and is the source of truth; these defaults are the fallback.

    PPO_<SECTION>_<FIELD>=value    e.g. PPO_TRAIN_MINIBATCH_SIZE=2048
    PPO_<NAME>=value               run-level, read by the entry script

`python -m ppo_transformer.config` prints every key, commented out.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

from model_common import env_config

PREFIX = "PPO"
ENV_FILE = Path(__file__).resolve().parent / ".env"
SECTIONS = ("net", "train")


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
    # Table rows beyond the catalog size, so new cards keep checkpoints loadable
    # (model_common/migrate_checkpoint.py grows past them).
    identity_capacity: int = 512
    effect_capacity: int = 320
    song_capacity: int = 64


@dataclass
class TrainConfig:
    vectorized_games: int = 512
    rollout_decisions: int = 65536      # samples collected per iteration
    minibatch_size: int = 1024
    ppo_epochs: int = 3
    clip_range: float = 0.2
    value_clip_range: float = 0.2
    # Stop the epochs once an epoch's mean approx_kl exceeds this; 0.0 disables.
    # Self-regulating: all epochs run again once KL falls.
    target_kl: float = 0.05
    value_loss_weight: float = 0.5
    entropy_bonus_initial: float = 0.01
    entropy_bonus_final: float = 0.001
    entropy_anneal_iterations: int = 400
    # The reward is terminal-only, so lambda sets how much of the outcome reaches
    # early decisions. 1.0 is pure Monte Carlo.
    gae_lambda: float = 0.98
    discount: float = 1.0               # terminal-only reward
    # CHAOS self-defeat terminal rewards: worse than a loss for the self-defeater,
    # far less than a win for the opponent (model_common.termination).
    self_defeat_loss_reward: float = -4.0
    self_defeat_win_reward: float = 0.25
    # 3e-4 collapsed entropy in a pilot run; this fallback must be a value known to work.
    learning_rate: float = 1e-4
    learning_rate_final: float = 1e-5
    warmup_iterations: int = 10
    learning_rate_decay_iterations: int = 1000  # cosine horizon; match planned iterations
    weight_decay: float = 1e-4
    gradient_clip: float = 1.0
    # Normalize advantages over the whole rollout, not per minibatch.
    normalize_advantage_per_batch: bool = True
    checkpoint_interval_iterations: int = 5
    # Newest checkpoints kept; promoted snapshots always stay. 0 keeps all.
    checkpoint_retention: int = 5
    # Master seed for deck draws, opponent choice and minibatch shuffling.
    seed: int = 19990929
    # Opponent mix per game.
    p_latest_vs_latest: float = 0.50
    p_vs_snapshot: float = 0.35
    # The remainder in RolloutCollector; validated with the others.
    p_vs_random: float = 0.15
    # Snapshot promotion gate.
    gating_games: int = 200
    gating_win_rate: float = 0.55
    snapshot_capacity: int = 30
    # Games against the greedy heuristic on gating iterations: the absolute strength
    # signal. Never gates promotion; 0 disables.
    benchmark_games: int = 100
    # Snapshot sampling: win-rate tracking speed, floor weight, and the bias from
    # evenly matched snapshots (0) toward ones the learner loses to (1).
    snapshot_win_rate_smoothing: float = 0.02
    snapshot_minimum_weight: float = 0.05
    snapshot_hardness_bias: float = 0.0
    # Real player decks (scripts/export_training_decks.py) without CHAOS cards, as
    # this stack never drafts. Resolved against the working directory (repo root).
    # With probability_user_deck 1.0 every game uses a real deck, and an empty pool
    # is fatal (train/rollout.py deck_sampler_for).
    deck_pool_path: str = 'data/training_decks_ppo.json'
    probability_user_deck: float = 1.0


@dataclass
class Config:
    net: NetConfig = field(default_factory=NetConfig)
    train: TrainConfig = field(default_factory=TrainConfig)

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> "Config":
        """Fail at load on inconsistent settings, not with a shape error mid-run."""
        env_config.check_probabilities_sum(
            {"p_latest_vs_latest": self.train.p_latest_vs_latest,
             "p_vs_snapshot": self.train.p_vs_snapshot,
             "p_vs_random": self.train.p_vs_random},
            "PPO_TRAIN opponent sampling")

        if not 0.0 <= self.train.probability_user_deck <= 1.0:
            raise ValueError(
                "PPO_TRAIN_PROBABILITY_USER_DECK must be within [0, 1], got "
                f"{self.train.probability_user_deck}")
        if not self.train.deck_pool_path:
            # An empty path is the working directory, which fails deep in the loader.
            raise ValueError(
                "PPO_TRAIN_DECK_POOL_PATH must name a pool file; an empty value "
                "reads as unset and resolves to the working directory")
        if not 0.0 <= self.train.gae_lambda <= 1.0:
            raise ValueError(
                f"PPO_TRAIN_GAE_LAMBDA must be within [0, 1], got {self.train.gae_lambda}")
        if self.train.self_defeat_loss_reward > -1.0:
            raise ValueError(
                "PPO_TRAIN_SELF_DEFEAT_LOSS_REWARD must be at most -1.0 (a "
                "normal loss) so a self-defeat is never the softer outcome, got "
                f"{self.train.self_defeat_loss_reward}")
        if not 0.0 <= self.train.self_defeat_win_reward <= 1.0:
            raise ValueError(
                "PPO_TRAIN_SELF_DEFEAT_WIN_REWARD must be within [0, 1], got "
                f"{self.train.self_defeat_win_reward}")
        if not 0.0 <= self.train.snapshot_hardness_bias <= 1.0:
            raise ValueError(
                "PPO_TRAIN_SNAPSHOT_HARDNESS_BIAS must be within [0, 1], got "
                f"{self.train.snapshot_hardness_bias}")
        if self.train.target_kl < 0.0:
            raise ValueError(
                "PPO_TRAIN_TARGET_KL must be at least 0.0 (0.0 disables the "
                f"early stop), got {self.train.target_kl}")
        if self.train.minibatch_size > self.train.rollout_decisions:
            raise ValueError(
                f"PPO_TRAIN_MINIBATCH_SIZE ({self.train.minibatch_size}) exceeds "
                f"PPO_TRAIN_ROLLOUT_DECISIONS ({self.train.rollout_decisions})")

        if self.net.embed_dim % self.net.num_heads:
            raise ValueError(
                f"PPO_NET_EMBED_DIM ({self.net.embed_dim}) must be divisible by "
                f"PPO_NET_NUM_HEADS ({self.net.num_heads})")
        if self.train.warmup_iterations >= self.train.learning_rate_decay_iterations:
            raise ValueError(
                f"PPO_TRAIN_WARMUP_ITERATIONS ({self.train.warmup_iterations}) must be "
                f"below PPO_TRAIN_LEARNING_RATE_DECAY_ITERATIONS "
                f"({self.train.learning_rate_decay_iterations})")

        try:
            from engine_alpha.cards import NUM_CARDS, NUM_EFFECTS, NUM_SONGS
        except Exception:
            return self
        if self.net.identity_capacity < NUM_CARDS:
            raise ValueError(
                f"PPO_NET_IDENTITY_CAPACITY ({self.net.identity_capacity}) is below "
                f"the card count ({NUM_CARDS}); see model_common/migrate_checkpoint.py")
        if self.net.effect_capacity < NUM_EFFECTS:
            raise ValueError(
                f"PPO_NET_EFFECT_CAPACITY ({self.net.effect_capacity}) is below "
                f"the effect count ({NUM_EFFECTS}); see model_common/migrate_checkpoint.py")
        if self.net.song_capacity < NUM_SONGS:
            raise ValueError(
                f"PPO_NET_SONG_CAPACITY ({self.net.song_capacity}) is below "
                f"the song count ({NUM_SONGS}); see model_common/migrate_checkpoint.py")
        return self


DEFAULT_CONFIG = Config()

RUN_SETTINGS: tuple[tuple[str, object, str], ...] = (
    ("runs_dir", "ppo_transformer/runs", "where checkpoints and snapshots live"),
    ("iterations", 1000, ""),
    ("device", None, "blank = cuda when available, else mps, else cpu"),
)


def load_config() -> Config:
    """Config with ppo_transformer/.env + environment overrides applied."""
    config = env_config.apply_env_overrides(Config(), PREFIX, SECTIONS, ENV_FILE)
    return config.validate()


def env_setting(name: str, default, target_type: type | None = None):
    """Run-level setting PPO_<NAME> from .env or the environment, else `default`."""
    return env_config.env_setting(name, default, PREFIX, ENV_FILE, target_type)


def format_template() -> str:
    """The full override surface as a `.env` body (every line commented)."""
    return env_config.format_env_template(
        Config(), PREFIX, SECTIONS, "ppo_transformer training parameters",
        "ppo_transformer.config", run_settings=RUN_SETTINGS)


if __name__ == "__main__":
    print(format_template())
