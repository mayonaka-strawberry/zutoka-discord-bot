"""Event-sink tests: attaching a sink never changes behavior, clones are
always silent, and the emitted stream is deterministic and well-formed."""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.effects import interpreter
from engine_alpha.events import (
    EVENT_BATTLE_RESULT, EVENT_CARDS_REVEALED, EVENT_CHRONOS_ADVANCED,
    EVENT_DRAW, EVENT_GAME_OVER, EVENT_MULLIGAN_DONE, EVENT_NAMES,
    EVENT_PHASE_CHANGED,
)
from engine_alpha.game import Game
from engine_alpha.state import PF_ATTACK_BONUS
from .conftest import make_vanilla_game, random_playout, random_vanilla_deck
from .test_invariants import snapshot
from .test_rulings import card_with_effect, fx, make_game

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


# ---------------------------------------------------------------------------
# Reveal events: the driver's only view of what a reveal effect exposed.
# ---------------------------------------------------------------------------

TAIDADA_CHARACTER_DEFS = [
    d.index for d in cards.CARD_DB
    if d.song == cards.SONG_NAMES.index("TAIDADA") and d.card_type == cards.TYPE_CHARACTER
]


def start_effect_on_battle_card(state, owner_index: int, effect_id: str) -> int:
    """Spawn the effect's carrier into the owner's battle zone and push its
    frame. Returns the instance id so a test can check the reported source."""
    instance_id = state.new_instance(card_with_effect(effect_id))
    state.players[owner_index].battle = instance_id
    interpreter.start_effect(state, owner_index, instance_id, fx(effect_id))
    return instance_id


def resolve_frame(state, choose: str) -> None:
    """Drain the frame stack, answering every request with the highest or the
    lowest legal action ("max" reveals everything, "min" reveals nothing)."""
    request = interpreter.resume(state, None, None)
    while request is not None:
        actions = request.legal_actions()
        answer = actions[-1] if choose == "max" else actions[0]
        request = interpreter.resume(state, request, answer)


def reveal_events(state) -> list[tuple]:
    return [event for event in state.event_sink if event[0] == EVENT_CARDS_REVEALED]


def game_with_taidada_hand(player_index: int, count: int):
    game = make_game()
    state = game.state
    state.event_sink = []
    player = state.players[player_index]
    player.hand.clear()
    for def_index in TAIDADA_CHARACTER_DEFS[:count]:
        player.hand.append(state.new_instance(def_index))
    return game, state, player


def test_reveal_reg_reports_the_picked_cards():
    game, state, owner = game_with_taidada_hand(0, 2)
    source = start_effect_on_battle_card(state, 0, "04-001")
    resolve_frame(state, "max")

    assert len(reveal_events(state)) == 1
    event = reveal_events(state)[0]
    assert event[1] == 0, "owner resolves the effect"
    assert event[2] == 0, "04-001 reveals the owner's own hand"
    assert event[3] == state.inst_def[source]
    assert sorted(event[4:]) == sorted(state.inst_def[i] for i in owner.hand)
    assert owner.flags[PF_ATTACK_BONUS] == 2 * 30


def test_reveal_reg_reports_an_empty_reveal():
    """The empty tail is what lets a driver say "nothing revealed" - without
    it the effect would resolve completely silently."""
    game, state, owner = game_with_taidada_hand(0, 2)
    start_effect_on_battle_card(state, 0, "04-001")
    resolve_frame(state, "min")

    assert len(reveal_events(state)) == 1
    assert len(reveal_events(state)[0]) == 4, "header only, no card indices"
    assert owner.flags[PF_ATTACK_BONUS] == 0


def test_reveal_hand_reports_the_opponent_hand_before_the_shuffle():
    game, state, opponent = game_with_taidada_hand(1, 3)
    hand_before = [state.inst_def[i] for i in opponent.hand]
    source = start_effect_on_battle_card(state, 0, "03-045")
    resolve_frame(state, "max")

    assert len(reveal_events(state)) == 1
    event = reveal_events(state)[0]
    assert event[1] == 0, "player 0 resolves the effect"
    assert event[2] == 1, "player 1's hand is the one exposed"
    assert event[3] == state.inst_def[source]
    assert list(event[4:]) == hand_before

    # Guards the assertion above against going vacuous: 03-045 shuffles right
    # after revealing, so capturing the hand post-shuffle has to be detectable.
    assert [state.inst_def[i] for i in opponent.hand] != hand_before


def test_reveal_events_are_silent_without_a_sink():
    """Reveals are observation-only, so a detached sink must make both ops
    behave exactly as they did before they emitted anything."""
    game, state, owner = game_with_taidada_hand(0, 2)
    state.event_sink = None
    start_effect_on_battle_card(state, 0, "04-001")
    resolve_frame(state, "max")

    assert state.event_sink is None
    assert owner.flags[PF_ATTACK_BONUS] == 2 * 30
