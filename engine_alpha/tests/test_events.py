"""Event-sink tests: attaching a sink never changes behavior, clones are
always silent, and the emitted stream is deterministic and well-formed."""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.events import (
    EVENT_BATTLE_RESULT, EVENT_CHRONOS_ADVANCED, EVENT_DRAW, EVENT_GAME_OVER,
    EVENT_MULLIGAN_DONE, EVENT_NAMES, EVENT_PHASE_CHANGED,
)
from engine_alpha.game import Game
from .conftest import make_vanilla_game, random_playout, random_vanilla_deck
from .test_invariants import snapshot

ALL_DEFS = [d.index for d in cards.CARD_DB]


def random_full_pool_deck(rng: random.Random) -> list[int]:
    """A legal 20-card deck drawn from the full card pool (10 distinct x 2)."""
    deck = []
    for def_index in rng.sample(ALL_DEFS, 10):
        deck.extend((def_index, def_index))
    return deck


def playout_with_sink(game: Game, action_seed: int) -> list[tuple]:
    game.state.event_sink = []
    random_playout(game, random.Random(action_seed))
    return game.state.event_sink


def test_sink_does_not_change_behavior_vanilla():
    for seed in range(10):
        deck_rng = random.Random(seed)
        baseline = make_vanilla_game(seed, deck_rng)
        random_playout(baseline, random.Random(seed))

        deck_rng = random.Random(seed)
        observed = make_vanilla_game(seed, deck_rng)
        observed.state.event_sink = []
        random_playout(observed, random.Random(seed))

        observed.state.event_sink = None
        assert snapshot(observed) == snapshot(baseline)


def test_sink_does_not_change_behavior_full_pool():
    for seed in range(15):
        deck_rng = random.Random(1000 + seed)
        decks = (random_full_pool_deck(deck_rng), random_full_pool_deck(deck_rng))

        baseline = Game(seed=seed, mode="fixed_decks", decks=decks)
        random_playout(baseline, random.Random(seed))

        observed = Game(seed=seed, mode="fixed_decks", decks=decks)
        observed.state.event_sink = []
        random_playout(observed, random.Random(seed))

        observed.state.event_sink = None
        assert snapshot(observed) == snapshot(baseline)


def test_clone_detaches_sink_and_stays_silent():
    game = make_vanilla_game(7)
    game.state.event_sink = []
    rng = random.Random(7)
    for _ in range(5):
        game.apply(rng.choice(game.legal_actions()))
    recorded_before_clone = list(game.state.event_sink)

    clone = game.clone()
    assert clone.state.event_sink is None
    random_playout(clone, random.Random(8))
    assert game.state.event_sink == recorded_before_clone

    fast_clone = game.state.fast_clone()
    assert fast_clone.event_sink is None


def test_event_stream_structure():
    game = make_vanilla_game(42)
    events = playout_with_sink(game, 42)

    assert events, "a full game must emit events"
    event_types = [event[0] for event in events]
    for event in events:
        assert event[0] in EVENT_NAMES
        assert all(isinstance(value, int) for value in event)

    assert event_types.count(EVENT_MULLIGAN_DONE) == 2
    assert EVENT_PHASE_CHANGED in event_types
    assert EVENT_BATTLE_RESULT in event_types
    assert EVENT_CHRONOS_ADVANCED in event_types
    assert EVENT_DRAW in event_types
    assert event_types[-1] == EVENT_GAME_OVER
    assert events[-1][1] == game.state.winner
    assert event_types.count(EVENT_GAME_OVER) == 1


def test_event_stream_deterministic():
    for seed in (3, 11):
        deck_rng = random.Random(seed)
        decks = (random_full_pool_deck(deck_rng), random_full_pool_deck(deck_rng))
        first = playout_with_sink(Game(seed=seed, mode="fixed_decks", decks=decks), seed)
        second = playout_with_sink(Game(seed=seed, mode="fixed_decks", decks=decks), seed)
        assert first == second
