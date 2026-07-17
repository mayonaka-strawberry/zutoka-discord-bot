"""CI-sized cross-engine equivalence check (the full gate is
run_equivalence.py with 10k games)."""

import random

from .equivalence.compare import check_game
from .equivalence.recorder import random_full_pool_deck


def test_equivalence_smoke():
    for seed in range(25):
        deck_rng = random.Random(seed ^ 0xDECC5)
        decks = (random_full_pool_deck(deck_rng), random_full_pool_deck(deck_rng))
        check_game(seed, decks)
