"""Presentation: every engine decision builds a well-formed request; the
compound caches translate pick lists into correct action sequences; the
set-cards legal sets are independent between players."""

from __future__ import annotations

import random

from engine_alpha.actions import (
    BINARY, SELECT_CARD, SELECT_IDENTITY, SELECT_NUMBER,
    P_MULLIGAN, P_SET_SLOT_A, P_SET_SLOT_B, select_card,
)
from engine_alpha.game import Game
from engine_alpha.state import PH_SET_CARDS
from zutomayo.match.decisions import (
    KIND_BINARY_CHOICE, KIND_CARD_CHOICE, KIND_CARD_MULTI_CHOICE,
    KIND_IDENTITY_INPUT, KIND_NUMBER_CHOICE, MatchDecisionRequest,
)
from zutomayo.match.discord_adapter import PendingSelection, DiscordMatchDecisionAdapter
from zutomayo.match.presentation import build_match_request, maximum_cards_to_set
from zutomayo.match.state_view import definition_index_to_card
from tests.match.support import random_full_pool_decks

EXPECTED_KIND = {
    SELECT_CARD: (KIND_CARD_CHOICE, KIND_CARD_MULTI_CHOICE),
    SELECT_IDENTITY: (KIND_IDENTITY_INPUT,),
    SELECT_NUMBER: (KIND_NUMBER_CHOICE,),
    BINARY: (KIND_BINARY_CHOICE,),
}


def test_every_pending_decision_builds_a_request():
    for seed in range(4):
        decks = random_full_pool_decks(100 + seed)
        game = Game(seed=seed, mode='fixed_decks', decks=decks)
        rng = random.Random(seed)
        steps = 0
        while not game.is_terminal() and steps < 400:
            engine_request = game.decision_context()
            request = build_match_request(game, engine_request, opponent_name='opp')
            assert request.kind in EXPECTED_KIND[engine_request.kind]
            assert request.player_index == game.state.acting
            assert request.action_count() == len(engine_request.legal_actions())
            assert request.prompt_text
            if engine_request.kind == SELECT_CARD:
                assert len(request.options) == len(engine_request.candidates)
                assert [option.action for option in request.options] == list(range(len(request.options)))
            game.apply(rng.choice(game.legal_actions()))
            steps += 1


def test_identity_validator_accepts_key_and_name():
    legal = [0, 1, 2]
    engine_request = None
    from engine_alpha.actions import select_identity, P_NAME_GUESS

    engine_request = select_identity(P_NAME_GUESS, legal)

    class GameStub:
        class state:
            acting = 0
            frame_stack = []

    request = build_match_request(GameStub, engine_request)
    card_zero = definition_index_to_card(0)
    assert request.validator(f'{card_zero.pack:02d}-{card_zero.id:03d}') == 0
    assert request.validator(f'{card_zero.pack}-{card_zero.id}') == 0
    assert request.validator(card_zero.name) == 0
    assert request.validator('definitely not a card') is None
    outside = definition_index_to_card(50)
    assert request.validator(f'{outside.pack:02d}-{outside.id:03d}') is None


def _selection_with(chosen: list[int]) -> PendingSelection:
    selection = PendingSelection()
    selection.resolve(chosen)
    return selection


def _request_for(purpose: int, candidates: list[int]) -> MatchDecisionRequest:
    engine_request = select_card(purpose, candidates, allow_pass=True)
    return MatchDecisionRequest(
        kind=KIND_CARD_MULTI_CHOICE, player_index=0, prompt_text='t',
        engine_request=engine_request, purpose=purpose,
    )


def test_mulligan_cache_produces_mark_sequence_then_pass():
    adapter = DiscordMatchDecisionAdapter(transport=None)
    selection = _selection_with([21, 23])

    first = _request_for(P_MULLIGAN, [20, 21, 22, 23, 24])
    assert adapter._compound_action(selection, first) == 1
    second = _request_for(P_MULLIGAN, [20, 22, 23, 24])
    assert adapter._compound_action(selection, second) == 2
    third = _request_for(P_MULLIGAN, [20, 22, 24])
    assert adapter._compound_action(selection, third) == 3  # PASS


def test_mulligan_cache_keep_hand_passes_immediately():
    adapter = DiscordMatchDecisionAdapter(transport=None)
    selection = _selection_with([])
    request = _request_for(P_MULLIGAN, [20, 21])
    assert adapter._compound_action(selection, request) == 2  # PASS


def test_set_cards_cache_slot_a_then_slot_b():
    adapter = DiscordMatchDecisionAdapter(transport=None)
    selection = _selection_with([31, 30])

    slot_a = _request_for(P_SET_SLOT_A, [30, 31, 32])
    assert adapter._compound_action(selection, slot_a) == 1
    slot_b = _request_for(P_SET_SLOT_B, [30, 32])
    assert adapter._compound_action(selection, slot_b) == 0


def test_set_cards_cache_single_pick_passes_slot_b():
    adapter = DiscordMatchDecisionAdapter(transport=None)
    selection = _selection_with([32])

    slot_a = _request_for(P_SET_SLOT_A, [30, 31, 32])
    assert adapter._compound_action(selection, slot_a) == 2
    slot_b = _request_for(P_SET_SLOT_B, [30, 31])
    assert adapter._compound_action(selection, slot_b) == 2  # PASS


def test_set_cards_cache_mismatch_returns_none():
    adapter = DiscordMatchDecisionAdapter(transport=None)
    selection = _selection_with([99])
    slot_a = _request_for(P_SET_SLOT_A, [30, 31, 32])
    assert adapter._compound_action(selection, slot_a) is None


def test_set_cards_legal_sets_are_independent_between_players():
    """The second mover's set-cards candidates must equal their own hand and
    be unaffected by the first mover's committed choice - the property that
    makes concurrent presentation safe."""
    for seed in range(6):
        decks = random_full_pool_decks(300 + seed)
        game = Game(seed=seed, mode='fixed_decks', decks=decks)
        rng = random.Random(seed)
        steps = 0
        while not game.is_terminal() and steps < 600:
            state = game.state
            if state.phase == PH_SET_CARDS and state.pending is not None \
                    and state.pending.purpose == P_SET_SLOT_A:
                first_mover = state.acting
                second_mover = 1 - first_mover
                second_hand_before = list(state.players[second_mover].hand)
                expected_maximum = maximum_cards_to_set(state, second_mover)

                probe = game.clone()
                while (not probe.is_terminal()
                       and probe.state.pending is not None
                       and probe.state.acting == first_mover
                       and probe.state.phase == PH_SET_CARDS):
                    probe.apply(rng.choice(probe.legal_actions()))
                if (probe.state.phase == PH_SET_CARDS
                        and probe.state.pending is not None
                        and probe.state.acting == second_mover):
                    assert list(probe.state.pending.candidates) == second_hand_before
                    assert maximum_cards_to_set(probe.state, second_mover) == expected_maximum
                break
            game.apply(rng.choice(game.legal_actions()))
            steps += 1
