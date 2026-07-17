"""Bulk cross-engine equivalence gate (M2).

Phase 1: N seeded games with random full-pool decks.
Phase 2: coverage-targeting — for every dispatchable effect resolved fewer
than THRESHOLD times, run games whose decks are stuffed with those cards
plus SEND-TO-POWER-2 support (so high costs get met), until all reach the
threshold or the game budget is exhausted.

Usage: python -m engine_alpha.tests.equivalence.run_equivalence [--games N]
"""

from __future__ import annotations

import argparse
import random
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from engine_alpha import cards
from engine_alpha.effects import interpreter
from engine_alpha.tests.equivalence.compare import check_game
from engine_alpha.tests.equivalence.recorder import random_full_pool_deck

THRESHOLD = 50


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=10000)
    parser.add_argument("--seed-base", type=int, default=100000)
    args = parser.parse_args()

    coverage: dict[int, int] = {}
    original_start = interpreter.start_effect

    def counting_start(state, owner, iid, effect_index):
        coverage[effect_index] = coverage.get(effect_index, 0) + 1
        original_start(state, owner, iid, effect_index)

    interpreter.start_effect = counting_start

    dispatchable = sorted(
        d.effect_index for d in cards.CARD_DB
        if d.effect_index != cards.NO_EFFECT
        and d.effect_id not in ("02-005", "02-007", "02-062")
    )
    stp2_pool = [d.index for d in cards.CARD_DB if d.send_to_power == 2]

    games_run = 0
    start_time = time.perf_counter()

    def progress(label: str) -> None:
        covered = sum(1 for e in dispatchable if coverage.get(e, 0) >= THRESHOLD)
        elapsed = time.perf_counter() - start_time
        print(f"[{elapsed:6.0f}s] {label}: {games_run} games equivalent; "
              f"{covered}/{len(dispatchable)} effects at >={THRESHOLD} resolutions",
              flush=True)

    # Phase 1: random decks
    phase1_games = int(args.games * 0.7)
    for game_number in range(phase1_games):
        seed = args.seed_base + game_number
        deck_rng = random.Random(seed ^ 0xDECC5)
        check_game(seed, (random_full_pool_deck(deck_rng), random_full_pool_deck(deck_rng)))
        games_run += 1
        if games_run % 500 == 0:
            progress("phase 1")

    progress("phase 1 done")

    # Phase 2: coverage targeting
    budget = args.games - phase1_games
    seed = args.seed_base + phase1_games
    while budget > 0:
        needy = [e for e in dispatchable if coverage.get(e, 0) < THRESHOLD]
        if not needy:
            break
        rng = random.Random(seed)
        batch = rng.sample(needy, min(6, len(needy)))
        target_defs = [cards.EFFECT_TO_CARD[e] for e in batch]
        support = [c for c in stp2_pool if c not in target_defs]
        picks = list(dict.fromkeys(target_defs + rng.sample(support, 8)))[:10]
        while len(picks) < 10:
            extra = rng.randrange(cards.NUM_CARDS)
            if extra not in picks:
                picks.append(extra)
        deck = []
        for p in picks:
            deck.extend((p, p))
        check_game(seed, (deck, list(deck)))
        games_run += 1
        budget -= 1
        seed += 1
        if games_run % 500 == 0:
            progress("phase 2")

    progress("final")
    needy = [(cards.EFFECT_IDS[e], coverage.get(e, 0))
             for e in dispatchable if coverage.get(e, 0) < THRESHOLD]
    if needy:
        print(f"BELOW THRESHOLD ({len(needy)}): {needy}")
        sys.exit(1)
    print("GATE PASS: all dispatchable effects resolved >= "
          f"{THRESHOLD} times across {games_run} equivalent games")


if __name__ == "__main__":
    main()
