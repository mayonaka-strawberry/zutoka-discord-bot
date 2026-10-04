"""Pins the batched MCTS search (alpha_zero.mcts.mcts.run_search_batched, on the live
play path) to a frozen copy of itself: both run on identical inputs and must build the
same tree from the same sequence of evaluator batches. The evaluator is a deterministic
hash-based stand-in, so no torch or checkpoint is needed.
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from alpha_zero.config import MCTSConfig  # noqa: E402
from alpha_zero.mcts import mcts as live_search  # noqa: E402
from engine_alpha.encoding.observation import encode  # noqa: E402
from engine_alpha.game import Game  # noqa: E402
from tests.match.support import random_full_pool_decks  # noqa: E402


# --- Frozen reference: a copy of the batched search in alpha_zero/mcts/mcts.py ---

class ReferenceNode:
    __slots__ = ("acting", "actions", "priors", "visit_counts", "value_sums",
                 "children", "terminal_value")

    def __init__(self) -> None:
        self.acting = -1
        self.actions: list[int] = []
        self.priors: np.ndarray | None = None
        self.visit_counts: np.ndarray | None = None
        self.value_sums: np.ndarray | None = None
        self.children: dict[int, ReferenceNode] = {}
        self.terminal_value: float | None = None  # player-0 frame

    @property
    def expanded(self) -> bool:
        return self.priors is not None or self.terminal_value is not None

    def expand(self, acting: int, actions: list[int], priors: np.ndarray) -> None:
        self.acting = acting
        self.actions = actions
        self.priors = priors
        self.visit_counts = np.zeros(len(actions), dtype=np.int32)
        self.value_sums = np.zeros(len(actions), dtype=np.float64)

    def select_child(self, cfg: MCTSConfig) -> int:
        total = self.visit_counts.sum()
        exploration = (cfg.c_puct_init
                       + math.log((total + cfg.c_puct_base + 1) / cfg.c_puct_base))
        sqrt_total = math.sqrt(total + 1e-8)
        q_values = np.divide(self.value_sums, self.visit_counts,
                             out=np.zeros_like(self.value_sums),
                             where=self.visit_counts > 0)
        if self.acting == 1:
            q_values = -q_values
        u_values = exploration * self.priors * sqrt_total / (1.0 + self.visit_counts)
        return int(np.argmax(q_values + u_values))

    def add_dirichlet_noise(self, cfg: MCTSConfig, rng: random.Random) -> None:
        n = len(self.actions)
        if n <= 1:
            return
        alpha = min(max(cfg.dirichlet_alpha_scale / n, cfg.dirichlet_alpha_min),
                    cfg.dirichlet_alpha_max)
        noise = np.random.default_rng(rng.randrange(2**31)).dirichlet([alpha] * n)
        self.priors = (1 - cfg.dirichlet_epsilon) * self.priors + cfg.dirichlet_epsilon * noise


def _reference_apply_virtual_loss(path, amount: int) -> None:
    for parent, child_index in path:
        parent.visit_counts[child_index] += amount
        parent.value_sums[child_index] += -amount if parent.acting == 0 else amount


def _reference_revert_virtual_loss(path, amount: int) -> None:
    for parent, child_index in path:
        parent.visit_counts[child_index] -= amount
        parent.value_sums[child_index] -= -amount if parent.acting == 0 else amount


def reference_run_search_batched(game, batch_evaluator, cfg: MCTSConfig, simulations: int,
                                 root: ReferenceNode | None = None,
                                 noise_rng: random.Random | None = None) -> ReferenceNode:
    if root is None or not root.expanded:
        root = ReferenceNode()
        if game.is_terminal():
            root.terminal_value = game.returns()[0]
            return root
        results = batch_evaluator([game])
        value_acting, priors = results[0]
        root.expand(game.current_player(), game.legal_actions(), priors)
    if noise_rng is not None:
        root.add_dirichlet_noise(cfg, noise_rng)

    simulations_done = 0
    while simulations_done < simulations:
        pending: list[tuple[ReferenceNode, list, object]] = []
        pending_ids: set[int] = set()
        budget = min(cfg.max_leaves_per_step, simulations - simulations_done)

        for _ in range(budget):
            node = root
            scratch = game.clone()
            path: list[tuple[ReferenceNode, int]] = []
            while node.expanded and node.terminal_value is None:
                child_index = node.select_child(cfg)
                path.append((node, child_index))
                action = node.actions[child_index]
                scratch.apply(action)
                child = node.children.get(action)
                if child is None:
                    child = ReferenceNode()
                    node.children[action] = child
                node = child
                if scratch.is_terminal():
                    node.terminal_value = scratch.returns()[0]
                    break

            if node.terminal_value is not None:
                for parent, child_index in path:
                    parent.visit_counts[child_index] += 1
                    parent.value_sums[child_index] += node.terminal_value
                simulations_done += 1
                continue
            if id(node) in pending_ids:
                _reference_apply_virtual_loss(path, 0)
                break
            _reference_apply_virtual_loss(path, cfg.virtual_loss)
            pending.append((node, path, scratch))
            pending_ids.add(id(node))

        if pending:
            results = batch_evaluator([scratch for _, _, scratch in pending])
            for (node, path, scratch), (value_acting, priors) in zip(pending, results):
                acting = scratch.current_player()
                node.expand(acting, scratch.legal_actions(), priors)
                value_p0 = value_acting if acting == 0 else -value_acting
                _reference_revert_virtual_loss(path, cfg.virtual_loss)
                for parent, child_index in path:
                    parent.visit_counts[child_index] += 1
                    parent.value_sums[child_index] += value_p0
                simulations_done += 1
        elif simulations_done < simulations and not pending:
            continue

    return root


# --- Deterministic evaluator and tree comparison ---

def _position_digest(game) -> bytes:
    tokens_int, tokens_float, globals_row, _ = encode(game)
    digest = hashlib.sha256()
    digest.update(tokens_int.tobytes())
    digest.update(tokens_float.tobytes())
    digest.update(globals_row.tobytes())
    digest.update(repr(game.legal_actions()).encode())
    return digest.digest()


class RecordingHashEvaluator:
    """Value and float32 priors derived from a hash of the position, with every
    batch it is asked for recorded as a list of position fingerprints."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def __call__(self, games: list) -> list[tuple[float, np.ndarray]]:
        fingerprints = []
        results = []
        for game in games:
            digest = _position_digest(game)
            fingerprints.append(digest.hex())
            generator = np.random.default_rng(int.from_bytes(digest[:8], 'little'))
            value = float(generator.uniform(-1.0, 1.0))
            weights = generator.random(len(game.legal_actions())).astype(np.float32)
            weights += np.float32(0.05)
            results.append((value, (weights / weights.sum()).astype(np.float32)))
        self.batches.append(fingerprints)
        return results


def _tree_signature(node) -> dict:
    """Everything a search leaves behind, as plain comparable values."""
    return {
        'acting': node.acting,
        'actions': list(node.actions),
        'priors': None if node.priors is None else node.priors.tolist(),
        'priors_dtype': None if node.priors is None else str(node.priors.dtype),
        'visit_counts': None if node.visit_counts is None else node.visit_counts.tolist(),
        'value_sums': None if node.value_sums is None else node.value_sums.tolist(),
        'terminal_value': node.terminal_value,
        'children': {action: _tree_signature(child)
                     for action, child in sorted(node.children.items())},
    }


def _walk(node):
    yield node
    for child in node.children.values():
        yield from _walk(child)


# --- Positions ---

@lru_cache(maxsize=None)
def _game_history(seed: int) -> tuple[int, ...]:
    """The action sequence of one complete random-play game."""
    game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    rng = random.Random(seed)
    actions = []
    while not game.is_terminal():
        action = rng.choice(game.legal_actions())
        actions.append(action)
        game.apply(action)
    return tuple(actions)


def _position(seed: int, moves_played: int) -> Game:
    game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    for action in _game_history(seed)[:moves_played]:
        game.apply(action)
    return game


def _position_at_fraction(seed: int, fraction: float) -> tuple[int, int]:
    return seed, int(len(_game_history(seed)) * fraction)


def _narrow_position(seed: int, maximum_legal: int) -> tuple[int, int]:
    """(seed, moves_played) for the first non-terminal position with between two
    and `maximum_legal` legal actions."""
    game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    for moves_played, action in enumerate(_game_history(seed)):
        if 2 <= len(game.legal_actions()) <= maximum_legal:
            return seed, moves_played
        game.apply(action)
    raise AssertionError(f'no position with 2..{maximum_legal} legal actions in seed {seed}')


def _run_both(make_game, cfg: MCTSConfig, simulations: int, noise_seed: int | None = None):
    reference_evaluator = RecordingHashEvaluator()
    live_evaluator = RecordingHashEvaluator()
    reference_root = reference_run_search_batched(
        make_game(), reference_evaluator, cfg, simulations,
        noise_rng=None if noise_seed is None else random.Random(noise_seed))
    live_root = live_search.run_search_batched(
        make_game(), live_evaluator, cfg, simulations,
        noise_rng=None if noise_seed is None else random.Random(noise_seed))
    assert _tree_signature(live_root) == _tree_signature(reference_root)
    assert live_evaluator.batches == reference_evaluator.batches
    return live_root, live_evaluator


# --- Cases ---

@pytest.mark.parametrize('seed, fraction', [(3, 0.3), (3, 0.6), (11, 0.4), (29, 0.8)])
def test_in_game_search_matches_reference(seed, fraction):
    seed, moves_played = _position_at_fraction(seed, fraction)
    _run_both(lambda: _position(seed, moves_played), MCTSConfig(), simulations=64)


def test_search_with_root_noise_matches_reference():
    seed, moves_played = _position_at_fraction(17, 0.5)
    _run_both(lambda: _position(seed, moves_played), MCTSConfig(), simulations=64,
              noise_seed=41)


def test_wide_draft_root_matches_reference():
    """The draft's first pick has every card in the catalog as a legal action."""
    root, _ = _run_both(lambda: Game(seed=7, mode='draft'), MCTSConfig(), simulations=40,
                        noise_seed=5)
    assert len(root.actions) > 100


def test_simulation_count_not_divisible_by_leaves_per_step_matches_reference():
    cfg = MCTSConfig()
    assert 50 % cfg.max_leaves_per_step != 0
    seed, moves_played = _position_at_fraction(5, 0.5)
    root, _ = _run_both(lambda: _position(seed, moves_played), cfg, simulations=50)
    assert int(root.visit_counts.sum()) == 50


def test_collisions_match_reference():
    """More leaves per step than the root has children forces in-flight
    collisions, which end a collection round early."""
    cfg = MCTSConfig(max_leaves_per_step=64)
    seed, moves_played = _narrow_position(13, maximum_legal=3)
    _, evaluator = _run_both(lambda: _position(seed, moves_played), cfg, simulations=100)
    later_batches = evaluator.batches[1:]
    assert later_batches and max(len(batch) for batch in later_batches) < cfg.max_leaves_per_step


@pytest.mark.parametrize('moves_before_end', [1, 3])
def test_near_terminal_search_matches_reference(moves_before_end):
    """Close to the end, simulations reach terminal states during leaf
    collection and are backed up without an evaluation."""
    seed = 19
    moves_played = len(_game_history(seed)) - moves_before_end
    root, _ = _run_both(lambda: _position(seed, moves_played), MCTSConfig(), simulations=64)
    assert any(node.terminal_value is not None for node in _walk(root))


def test_terminal_root_matches_reference():
    seed = 23
    moves_played = len(_game_history(seed))
    root, evaluator = _run_both(lambda: _position(seed, moves_played), MCTSConfig(),
                                simulations=16)
    assert root.terminal_value is not None
    assert evaluator.batches == []


def test_reused_root_with_noise_matches_reference():
    """The self-play pattern: search, play the most visited move, then search
    again from that move's subtree with fresh root noise."""
    cfg = MCTSConfig()
    seed, moves_played = _position_at_fraction(31, 0.4)

    def two_searches(search, first_noise_seed, second_noise_seed):
        game = _position(seed, moves_played)
        evaluator = RecordingHashEvaluator()
        root = search(game, evaluator, cfg, 48, noise_rng=random.Random(first_noise_seed))
        action = root.actions[int(np.argmax(root.visit_counts))]
        game.apply(action)
        reused = root.children.get(action)
        assert reused is not None and reused.expanded
        second_root = search(game, evaluator, cfg, 48, root=reused,
                             noise_rng=random.Random(second_noise_seed))
        return second_root, evaluator.batches

    reference_root, reference_batches = two_searches(reference_run_search_batched, 3, 4)
    live_root, live_batches = two_searches(live_search.run_search_batched, 3, 4)
    assert _tree_signature(live_root) == _tree_signature(reference_root)
    assert live_batches == reference_batches
