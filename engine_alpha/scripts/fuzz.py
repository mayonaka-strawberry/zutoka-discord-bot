"""Long-running invariant fuzzer (M1 gate: 1M steps clean).

Plays random games (mixing draft and fixed-deck modes) checking invariants
at every decision. On violation, prints the seed and action trace for exact
reproduction and exits non-zero.

Usage: python -m engine_alpha.scripts.fuzz [--steps N]
"""

from __future__ import annotations

import argparse
import random
import sys
import time

from engine_alpha.game import Game
from engine_alpha.tests.conftest import random_vanilla_deck
from engine_alpha.tests.test_invariants import check_invariants


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=1_000_000)
    args = parser.parse_args()

    meta_rng = random.Random(20260706)
    steps_done = 0
    games_done = 0
    start = time.perf_counter()

    while steps_done < args.steps:
        game_seed = meta_rng.randrange(2**32)
        action_seed = meta_rng.randrange(2**32)
        use_draft = meta_rng.random() < 0.5
        action_rng = random.Random(action_seed)
        if use_draft:
            game = Game(seed=game_seed, mode="draft")
        else:
            deck_rng = random.Random(action_seed ^ 0xDECC)
            game = Game(seed=game_seed, mode="fixed_decks",
                        decks=(random_vanilla_deck(deck_rng), random_vanilla_deck(deck_rng)))
        trace: list[int] = []
        try:
            check_invariants(game)
            while not game.is_terminal():
                action = action_rng.choice(game.legal_actions())
                trace.append(action)
                game.apply(action)
                check_invariants(game)
                steps_done += 1
        except Exception:
            print(f"INVARIANT VIOLATION: mode={'draft' if use_draft else 'fixed'} "
                  f"game_seed={game_seed} action_seed={action_seed}")
            print(f"trace ({len(trace)} actions): {trace}")
            raise
        games_done += 1

    elapsed = time.perf_counter() - start
    print(f"clean: {steps_done} steps across {games_done} games in {elapsed:.1f}s "
          f"({steps_done / elapsed:.0f} checked steps/s)")
    sys.exit(0)


if __name__ == "__main__":
    main()
