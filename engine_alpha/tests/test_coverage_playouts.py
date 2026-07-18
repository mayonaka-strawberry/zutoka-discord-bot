"""Breadth playouts: several hundred seeded random full-pool games, plus a
batch of draft-mode games. Exists to exercise the long tail of effect IR
programs and custom handlers on every run (the engine plays thousands of
games per second, so this stays fast)."""

from __future__ import annotations

import random

from engine_alpha.game import Game
from .conftest import random_playout
from .test_events import random_full_pool_deck

FULL_POOL_GAMES = 300
DRAFT_GAMES = 15


def test_full_pool_random_playouts():
    for seed in range(FULL_POOL_GAMES):
        deck_rng = random.Random(50000 + seed)
        decks = (random_full_pool_deck(deck_rng), random_full_pool_deck(deck_rng))
        game = Game(seed=seed, mode='fixed_decks', decks=decks)
        random_playout(game, random.Random(seed))
        assert game.state.winner in (0, 1, 2)


def test_draft_mode_random_playouts():
    for seed in range(DRAFT_GAMES):
        game = Game(seed=70000 + seed, mode='draft')
        random_playout(game, random.Random(seed))
        assert game.state.winner in (0, 1, 2)
