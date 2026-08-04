"""M1 gate: property tests and invariant fuzzing over random playouts.

Invariants checked at every decision point:
- card conservation: each player's 20 instances are all in exactly one zone
- chronos in [0, 18); HP in [0, 100]
- a pending decision always has at least one legal action
- clone independence: mutating the original never changes a clone
"""

from __future__ import annotations

import pickle
import random

from engine_alpha import cards
from engine_alpha.game import Game
from engine_alpha.state import PH_GAME_OVER
from .conftest import make_vanilla_game, random_playout, random_vanilla_deck


def snapshot(game: Game) -> tuple:
    """Canonical value-snapshot of everything that defines the game state."""
    state = game.state
    players = tuple(
        (p.index, p.side_is_night, p.hp, tuple(p.deck), tuple(p.hand),
         tuple(p.charger), tuple(p.abyss), p.battle, p.set_a, p.set_b, p.set_c,
         p.cards_played, p.area_blocked, p.hand_bonus, p.pending_hand_bonus,
         p.prev_battle_def, p.swapped_from_songs, tuple(p.flags))
        for p in state.players
    )
    draft = None
    if state.draft is not None:
        draft = (state.draft.pick_number, tuple(state.draft.decks[0]), tuple(state.draft.decks[1]))
    return (
        state.phase, state.turn, state.chronos, state.chronos_at_turn_start,
        players, state.last_battle_winner, state.winner,
        tuple(state.inst_def), tuple(state.inst_played), tuple(state.inst_neg),
        tuple(state.inst_cost_red), tuple(state.inst_attr_ovr), tuple(state.inst_face_up),
        state.acting, tuple(tuple(c) if isinstance(c, list) else c for c in state.phase_ctx),
        tuple(state.gflags), state.rng_key, state.rng_ctr, draft,
    )


def check_invariants(game: Game) -> None:
    state = game.state
    assert 0 <= state.chronos < 18
    total_instances = len(state.inst_def)
    seen: set[int] = set()
    for player in state.players:
        assert 0 <= player.hp <= 100
        zone_lists = [player.deck, player.hand, player.charger, player.abyss]
        singles = [player.battle, player.set_a, player.set_b, player.set_c]
        player_instances = [i for zone in zone_lists for i in zone] + [i for i in singles if i != -1]
        for instance_id in player_instances:
            assert 0 <= instance_id < total_instances
            assert instance_id not in seen, "instance present in two zones"
            seen.add(instance_id)
    if state.draft is None and total_instances:
        # After setup every created instance lives in exactly one zone.
        assert len(seen) == total_instances, "instance vanished from all zones"
    if not game.is_terminal():
        assert game.legal_actions(), "pending decision with no legal action"
    else:
        assert state.phase == PH_GAME_OVER


def test_invariants_vanilla_fixed_decks():
    rng = random.Random(123)
    for seed in range(40):
        game = make_vanilla_game(seed, rng)
        check_invariants(game)
        random_playout(game, rng, on_step=check_invariants)
        assert game.state.winner in (0, 1, 2)


def test_invariants_draft_mode():
    rng = random.Random(321)
    for seed in range(25):
        game = Game(seed=seed, mode="draft")
        check_invariants(game)
        random_playout(game, rng, on_step=check_invariants)
        assert game.state.winner in (0, 1, 2)
        # Drafted decks were legal: 20 instances per player
        counts = [0, 0]
        state = game.state
        for player in state.players:
            for zone in (player.deck, player.hand, player.charger, player.abyss):
                counts[player.index] += len(zone)
            for single in (player.battle, player.set_a, player.set_b, player.set_c):
                if single != -1:
                    counts[player.index] += 1
        assert counts == [20, 20]


def test_clone_independence():
    rng = random.Random(99)
    for seed in range(10):
        game = make_vanilla_game(seed, rng)
        # Advance to a mid-game point
        for _ in range(rng.randrange(5, 40)):
            if game.is_terminal():
                break
            game.apply(rng.choice(game.legal_actions()))
        clone = game.clone()
        frozen = snapshot(clone)
        assert snapshot(game) == frozen
        # Mutate the original heavily; the clone must not move.
        if not game.is_terminal():
            random_playout(game, rng)
        assert snapshot(clone) == frozen
        # The clone must be playable to completion on its own.
        if not clone.is_terminal():
            random_playout(clone, rng)


def test_clone_determinism():
    """A clone given the same actions reaches the same states (shared RNG)."""
    rng = random.Random(4242)
    game = Game(seed=777, mode="draft")
    for _ in range(15):
        game.apply(rng.choice(game.legal_actions()))
    clone = game.clone()
    action_rng_a, action_rng_b = random.Random(5), random.Random(5)
    while not game.is_terminal():
        game.apply(action_rng_a.choice(game.legal_actions()))
    while not clone.is_terminal():
        clone.apply(action_rng_b.choice(clone.legal_actions()))
    assert snapshot(game) == snapshot(clone)


def test_pickle_round_trip():
    rng = random.Random(55)
    game = make_vanilla_game(3, rng)
    for _ in range(20):
        if game.is_terminal():
            break
        game.apply(rng.choice(game.legal_actions()))
    restored = pickle.loads(pickle.dumps(game))
    assert snapshot(restored) == snapshot(game)
    random_playout(restored, rng)


def test_pickle_round_trip_carries_self_defeat_fields():
    """The self-defeat slots are outside snapshot(), so pin them separately: a
    resumed game must still know the loser threw it."""
    game = make_vanilla_game(3, random.Random(55))
    game.state.self_defeat_player = 1
    game.state.self_defeat_turn = 1
    restored = pickle.loads(pickle.dumps(game))
    assert restored.state.self_defeat_player == 1
    assert restored.state.self_defeat_turn == 1

    fresh = pickle.loads(pickle.dumps(make_vanilla_game(4, random.Random(56))))
    assert fresh.state.self_defeat_player == -1
    assert fresh.state.self_defeat_turn == -1


def test_same_seed_same_game():
    rng_a, rng_b = random.Random(1), random.Random(1)
    game_a = Game(seed=42, mode="draft")
    game_b = Game(seed=42, mode="draft")
    while not game_a.is_terminal():
        action = rng_a.choice(game_a.legal_actions())
        game_a.apply(action)
        game_b.apply(action)
    assert game_b.is_terminal()
    assert snapshot(game_a) == snapshot(game_b)


def test_night_player_override_only_changes_sides():
    """The TCG series flow overrides the side coin flip after game 1. The RNG
    draw must still be consumed, so everything except the side assignment is
    identical to a flipped game."""
    rng = random.Random(9)
    decks = (random_vanilla_deck(rng), random_vanilla_deck(rng))

    flipped = Game(seed=99, mode="fixed_decks", decks=decks)
    explicit_none = Game(seed=99, mode="fixed_decks", decks=decks, night_player=None)
    assert snapshot(explicit_none) == snapshot(flipped)

    for chosen in (0, 1):
        overridden = Game(seed=99, mode="fixed_decks", decks=decks, night_player=chosen)
        assert overridden.state.players[chosen].side_is_night is True
        assert overridden.state.players[1 - chosen].side_is_night is False
        # The coin-flip draw is still consumed, so shuffles and hands match.
        assert overridden.state.rng_ctr == flipped.state.rng_ctr
        for player, flipped_player in zip(overridden.state.players, flipped.state.players):
            assert player.deck == flipped_player.deck
            assert player.hand == flipped_player.hand


def test_night_player_override_rejects_bad_index():
    rng = random.Random(10)
    decks = (random_vanilla_deck(rng), random_vanilla_deck(rng))
    try:
        Game(seed=1, mode="fixed_decks", decks=decks, night_player=2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for night_player=2")


def test_deck_validation():
    deck = random_vanilla_deck(random.Random(0))
    bad = deck[:19] + [deck[0]]  # 3 copies of the first card
    try:
        Game(seed=1, mode="fixed_decks", decks=(bad, deck))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for illegal deck")
