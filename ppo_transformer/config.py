"""All ppo_transformer hyperparameters as one dataclass tree.

The defaults here are the tracked baseline; `ppo_transformer/.env` (or the
process environment) layers per-machine overrides on top via `load_config()`:

    PPO_<SECTION>_<FIELD>=value    e.g. PPO_TRAIN_MINIBATCH_SIZE=2048
    PPO_<NAME>=value               run-level, read by the entry script

To see every key with its current default:

    python -m ppo_transformer.config
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
    # The reward is terminal-only, so lambda controls how much of the actual
    # game outcome reaches early decisions: at 0.95 a decision 50 steps from
    # the end sees it at weight 0.08, at 0.98 it sees 0.36. 1.0 is pure Monte
    # Carlo (unbiased, higher variance).
    gae_lambda: float = 0.98
    discount: float = 1.0               # terminal-only reward
    # CHAOS bank-or-lose cards end the game immediately when the Abyss
    # minimum is not met. That is a self-inflicted blunder rather than a
    # normal loss, so the terminal reward is shaped for both seats: the
    # self-defeating player is punished harder than a normal loss, and the
    # opponent is credited far less than an earned win so free wins are not
    # something the policy learns to play for. Applies only to that specific
    # termination (see model_common.termination.chaos_self_defeat_loser).
    self_defeat_loss_reward: float = -2.0
    self_defeat_win_reward: float = 0.25
    learning_rate: float = 3e-4
    learning_rate_final: float = 3e-5
    warmup_iterations: int = 10
    learning_rate_decay_iterations: int = 1000  # cosine horizon; match planned iterations
    weight_decay: float = 1e-4
    gradient_clip: float = 1.0
    # Normalize advantages once over the whole rollout rather than per
    # minibatch, so the advantage scale is consistent across an epoch.
    normalize_advantage_per_batch: bool = True
    checkpoint_interval_iterations: int = 5
    # Checkpoints kept on disk, newest first. Promoted snapshots are always
    # retained regardless. 0 disables pruning.
    checkpoint_retention: int = 5
    # Master seed for deck draws, opponent choice and minibatch shuffling.
    seed: int = 19990929
    # Opponent mix per game.
    p_latest_vs_latest: float = 0.50
    p_vs_snapshot: float = 0.35
    # Implicit remainder in RolloutCollector; validated to stay consistent.
    p_vs_random: float = 0.15
    # Snapshot promotion gate.
    gating_games: int = 200
    gating_win_rate: float = 0.55
    snapshot_capacity: int = 30
    # Snapshot sampling: how fast the learner's per-snapshot win rate tracks
    # results, the floor weight so no snapshot is starved, and the shift from
    # variance weighting (0) toward preferring snapshots the learner loses to (1).
    snapshot_win_rate_smoothing: float = 0.02
    snapshot_minimum_weight: float = 0.05
    snapshot_hardness_bias: float = 0.0
    # Deck source per game. The pool is the exported set of real player decks
    # (scripts/export_training_decks.py); the remainder are generated random
    # legal decks so unplayed cards still receive gradient. An empty path means
    # model_common.deck_pool.DEFAULT_DECK_POOL_PATH; a missing file falls back
    # to random decks for every game.
    deck_pool_path: str = ''
    probability_user_deck: float = 0.75


@dataclass
class Config:
    net: NetConfig = field(default_factory=NetConfig)
    train: TrainConfig = field(default_factory=TrainConfig)

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> "Config":
        """Fails loudly on inconsistent settings — see the alpha_zero
        equivalent for why this runs at config load."""
        env_config.check_probabilities_sum(
            {"p_latest_vs_latest": self.train.p_latest_vs_latest,
             "p_vs_snapshot": self.train.p_vs_snapshot,
             "p_vs_random": self.train.p_vs_random},
            "PPO_TRAIN opponent sampling")

        if not 0.0 <= self.train.probability_user_deck <= 1.0:
            raise ValueError(
                "PPO_TRAIN_PROBABILITY_USER_DECK must be within [0, 1], got "
                f"{self.train.probability_user_deck}")
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
            from engine_alpha.cards import NUM_CARDS, NUM_EFFECTS
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
    """Run-level setting: PPO_<NAME> from .env/environment, else default."""
    return env_config.env_setting(name, default, PREFIX, ENV_FILE, target_type)


def format_template() -> str:
    """The full override surface as a `.env` body (every line commented)."""
    return env_config.format_env_template(
        Config(), PREFIX, SECTIONS, "ppo_transformer training parameters",
        "ppo_transformer.config", run_settings=RUN_SETTINGS)


if __name__ == "__main__":
    print(format_template())
