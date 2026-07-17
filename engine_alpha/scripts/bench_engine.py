"""Engine microbenchmarks (M1/M2 performance gate).

Targets (plan section 6): random-policy full game >= 150 games/s/core,
Game.clone() <= 20 us at mid-game.

Usage: python -m engine_alpha.scripts.bench_engine [--games N]
"""

from __future__ import annotations

import argparse
import random
import statistics
import time

from engine_alpha.game import Game
from engine_alpha.tests.conftest import random_vanilla_deck


def bench_games(n_games: int, mode: str) -> tuple[float, float]:
    rng = random.Random(1234)
    steps_total = 0
    start = time.perf_counter()
    for seed in range(n_games):
        if mode == "draft":
            game = Game(seed=seed, mode="draft")
        else:
            game = Game(seed=seed, mode="fixed_decks",
                        decks=(random_vanilla_deck(rng), random_vanilla_deck(rng)))
        while not game.is_terminal():
            game.apply(rng.choice(game.legal_actions()))
            steps_total += 1
    elapsed = time.perf_counter() - start
    return n_games / elapsed, steps_total / elapsed


def bench_clone(n_clones: int = 5000) -> float:
    rng = random.Random(99)
    game = Game(seed=7, mode="fixed_decks",
                decks=(random_vanilla_deck(rng), random_vanilla_deck(rng)))
    # Advance to mid-game
    for _ in range(30):
        if game.is_terminal():
            break
        game.apply(rng.choice(game.legal_actions()))
    times = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(n_clones):
            game.clone()
        times.append((time.perf_counter() - start) / n_clones)
    return min(times) * 1e6


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=200)
    args = parser.parse_args()

    games_per_s, steps_per_s = bench_games(args.games, "fixed")
    print(f"fixed-deck random game: {games_per_s:8.1f} games/s   {steps_per_s:9.0f} decisions/s")
    games_per_s_draft, steps_per_s_draft = bench_games(args.games, "draft")
    print(f"draft-mode random game: {games_per_s_draft:8.1f} games/s   {steps_per_s_draft:9.0f} decisions/s")
    clone_us = bench_clone()
    print(f"mid-game clone:         {clone_us:8.2f} us")
    print()
    ok_games = games_per_s >= 150
    ok_clone = clone_us <= 20
    print(f"gate games/s >= 150: {'PASS' if ok_games else 'FAIL'}")
    print(f"gate clone <= 20us:  {'PASS' if ok_clone else 'FAIL'}")


if __name__ == "__main__":
    main()
