"""Shared fixtures and helpers for engine_alpha tests."""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.game import Game

# All 172 effect-less cards are characters: every enchant and area enchant
# in the card pool carries an effect. Vanilla decks are therefore
# all-character, which suits M1 (effects are stubbed).
VANILLA_DEFS = [d.index for d in cards.CARD_DB if d.effect_index == cards.NO_EFFECT]


def random_vanilla_deck(rng: random.Random) -> list[int]:
    """A legal 20-card deck of effect-less cards (10 distinct x 2 copies)."""
    deck = []
    for def_index in rng.sample(VANILLA_DEFS, 10):
        deck.extend((def_index, def_index))
    return deck


def random_playout(game: Game, rng: random.Random, max_steps: int = 50000,
                   on_step=None) -> int:
    """Play a game to the end with uniform-random actions. Returns steps."""
    steps = 0
    while not game.is_terminal():
        actions = game.legal_actions()
        assert actions, f"no legal actions in phase {game.state.phase}"
        game.apply(rng.choice(actions))
        steps += 1
        if on_step is not None:
            on_step(game)
        if steps > max_steps:
            raise AssertionError("runaway game")
    return steps


def make_vanilla_game(seed: int, rng: random.Random | None = None) -> Game:
    rng = rng or random.Random(seed)
    return Game(seed=seed, mode="fixed_decks",
                decks=(random_vanilla_deck(rng), random_vanilla_deck(rng)))
