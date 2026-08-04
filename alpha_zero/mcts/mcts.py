"""PUCT search over the resumable game.

Values are stored in the fixed player-0 frame and converted to the acting
player's perspective at selection time — required because consecutive nodes
frequently belong to the SAME player (micro-decision chains: mulligan marks,
set A then B, effect ordering, multi-op effects), so a naive
sign-flip-per-ply would be wrong.

No chance nodes: shuffles are deterministic functions of the state's RNG
counter, so every branch through a state agrees on their outcomes.
"""

from __future__ import annotations

import math
import random

import numpy as np

from ..config import MCTSConfig


class Node:
    __slots__ = ("acting", "actions", "priors", "visit_counts", "value_sums",
                 "children", "terminal_value")

    def __init__(self) -> None:
        self.acting = -1
        self.actions: list[int] = []
        self.priors: np.ndarray | None = None
        self.visit_counts: np.ndarray | None = None
        self.value_sums: np.ndarray | None = None
        self.children: dict[int, Node] = {}
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
        """Returns the index into self.actions maximizing PUCT."""
        total = self.visit_counts.sum()
        exploration = (cfg.c_puct_init
                       + math.log((total + cfg.c_puct_base + 1) / cfg.c_puct_base))
        sqrt_total = math.sqrt(total + 1e-8)
        # Q in player-0 frame -> acting player's perspective
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


def run_search(game, evaluator, cfg: MCTSConfig, simulations: int,
               root: Node | None = None, noise_rng: random.Random | None = None) -> Node:
    """Runs PUCT simulations from `game`'s current state. Returns the root.

    evaluator(game) -> (value_for_acting_player: float, priors: np.ndarray
    over legal_actions()). Values are internally converted to the player-0
    frame for storage.
    """
    if root is None or not root.expanded:
        root = Node()
        _expand(root, game, evaluator)
    if noise_rng is not None:
        root.add_dirichlet_noise(cfg, noise_rng)

    for _ in range(simulations):
        node = root
        scratch = game.clone()
        path: list[tuple[Node, int]] = []

        while node.expanded and node.terminal_value is None:
            child_index = node.select_child(cfg)
            path.append((node, child_index))
            action = node.actions[child_index]
            scratch.apply(action)
            child = node.children.get(action)
            if child is None:
                child = Node()
                node.children[action] = child
            node = child
            if scratch.is_terminal():
                node.terminal_value = scratch.returns()[0]
                break

        if node.terminal_value is not None:
            value_p0 = node.terminal_value
        else:
            value_p0 = _expand(node, scratch, evaluator)

        for parent, child_index in path:
            parent.visit_counts[child_index] += 1
            parent.value_sums[child_index] += value_p0

    return root


def _expand(node: Node, game, evaluator) -> float:
    """Expands the node and returns the leaf value in the player-0 frame."""
    if game.is_terminal():
        node.terminal_value = game.returns()[0]
        return node.terminal_value
    acting = game.current_player()
    value_acting, priors = evaluator(game)
    node.expand(acting, game.legal_actions(), priors)
    return value_acting if acting == 0 else -value_acting


def _apply_virtual_loss(path: list[tuple["Node", int]], amount: int) -> None:
    """Discourage in-flight paths: worsen Q for the selecting player at
    each edge (player-0 frame: subtract for acting 0, add for acting 1)."""
    for parent, child_index in path:
        parent.visit_counts[child_index] += amount
        parent.value_sums[child_index] += -amount if parent.acting == 0 else amount


def _revert_virtual_loss(path: list[tuple["Node", int]], amount: int) -> None:
    for parent, child_index in path:
        parent.visit_counts[child_index] -= amount
        parent.value_sums[child_index] -= -amount if parent.acting == 0 else amount


def run_search_batched(game, batch_evaluator, cfg: MCTSConfig, simulations: int,
                       root: Node | None = None,
                       noise_rng: random.Random | None = None) -> Node:
    """PUCT with batched leaf evaluation: collects up to
    cfg.max_leaves_per_step distinct leaves under virtual loss, evaluates
    them in one call, then backs all of them up.

    batch_evaluator(games: list) -> list of (value_acting, priors).
    """
    if root is None or not root.expanded:
        root = Node()
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
        pending: list[tuple[Node, list, object]] = []  # (leaf, path, scratch)
        pending_ids: set[int] = set()
        budget = min(cfg.max_leaves_per_step, simulations - simulations_done)

        for _ in range(budget):
            node = root
            scratch = game.clone()
            path: list[tuple[Node, int]] = []
            while node.expanded and node.terminal_value is None:
                child_index = node.select_child(cfg)
                path.append((node, child_index))
                action = node.actions[child_index]
                scratch.apply(action)
                child = node.children.get(action)
                if child is None:
                    child = Node()
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
                # Collision with an in-flight leaf despite virtual loss; the
                # cheapest correct move is to stop collecting this round.
                _apply_virtual_loss(path, 0)
                break
            _apply_virtual_loss(path, cfg.virtual_loss)
            pending.append((node, path, scratch))
            pending_ids.add(id(node))

        if pending:
            results = batch_evaluator([scratch for _, _, scratch in pending])
            for (node, path, scratch), (value_acting, priors) in zip(pending, results):
                acting = scratch.current_player()
                node.expand(acting, scratch.legal_actions(), priors)
                value_p0 = value_acting if acting == 0 else -value_acting
                _revert_virtual_loss(path, cfg.virtual_loss)
                for parent, child_index in path:
                    parent.visit_counts[child_index] += 1
                    parent.value_sums[child_index] += value_p0
                simulations_done += 1
        elif simulations_done < simulations and not pending:
            # Only terminal paths were found this round; counts already added.
            continue

    return root


def select_action(root: Node, temperature: float, rng: random.Random,
                  use_gumbel: bool = False) -> int:
    """Samples an action from the root visit distribution.

    With use_gumbel (small simulation budgets), ties and near-ties among
    visit counts are broken by prior-plus-Gumbel scores instead of index
    order, which improves the played move when visits are too sparse to
    discriminate. Wired from MCTSConfig.use_gumbel_root."""
    counts = root.visit_counts.astype(np.float64)
    if temperature <= 0.01:
        if use_gumbel:
            noise_rng = np.random.default_rng(rng.randrange(2**31))
            gumbel = noise_rng.gumbel(size=len(counts))
            with np.errstate(divide="ignore"):
                scores = counts + 1e-3 * (np.log(root.priors + 1e-12) + gumbel)
            return root.actions[int(np.argmax(scores))]
        return root.actions[int(np.argmax(counts))]
    weights = counts ** (1.0 / temperature)
    total = weights.sum()
    if total <= 0:
        return rng.choice(root.actions)
    probabilities = weights / total
    choice = np.random.default_rng(rng.randrange(2**31)).choice(len(counts), p=probabilities)
    return root.actions[int(choice)]


def reuse_subtree(root: Node | None, action: int) -> Node | None:
    """Re-root the tree at the chosen action's child (subtree reuse)."""
    if root is None:
        return None
    return root.children.get(action)


def visit_policy(root: Node) -> tuple[list[int], np.ndarray]:
    """(actions, normalized visit distribution) — the training policy target."""
    counts = root.visit_counts.astype(np.float32)
    total = counts.sum()
    if total <= 0:
        counts = np.ones_like(counts)
        total = counts.sum()
    return root.actions, counts / total
