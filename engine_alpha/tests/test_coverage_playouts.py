"""Breadth playouts: several hundred seeded random full-pool games, plus a
batch of draft-mode games. Exists to exercise the long tail of effect IR
programs and custom handlers on every run (the engine plays thousands of
games per second, so this stays fast)."""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.game import Game
from .conftest import random_playout
from .test_events import random_full_pool_deck

FULL_POOL_GAMES = 300
DRAFT_GAMES = 15
FORCED_EFFECT_GAMES = 40
# Effects worth guaranteeing coverage for rather than waiting on a lucky
# full-pool sample: the ones whose programs branch into a self-defeat or
# empty a zone wholesale.
FORCED_EFFECT_IDS = ('04-105', '04-106', '04-107')


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


def _deck_containing(forced_defs: list[int], rng: random.Random) -> list[int]:
    """A legal 20-card deck (10 distinct x 2) that always holds `forced_defs`."""
    distinct = list(forced_defs)
    for def_index in rng.sample(range(cards.NUM_CARDS), 10 + len(distinct)):
        if len(distinct) == 10:
            break
        if def_index not in distinct:
            distinct.append(def_index)
    deck = []
    for def_index in distinct:
        deck.extend((def_index, def_index))
    return deck


def test_forced_new_effect_random_playouts():
    """Both decks are guaranteed to hold 04-105 / 04-106 / 04-107, so their
    programs run under uniform-random play on every test run instead of
    waiting for a lucky full-pool draw."""
    forced = [cards.EFFECT_TO_CARD[cards.EFFECT_TO_INDEX[effect_id]]
              for effect_id in FORCED_EFFECT_IDS]
    for seed in range(FORCED_EFFECT_GAMES):
        deck_rng = random.Random(90000 + seed)
        decks = (_deck_containing(forced, deck_rng),
                 _deck_containing(forced, deck_rng))
        game = Game(seed=seed, mode='fixed_decks', decks=decks)
        random_playout(game, random.Random(seed))
        assert game.state.winner in (0, 1, 2)
