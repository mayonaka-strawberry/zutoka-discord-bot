"""Presentation: every engine decision builds a well-formed request; the name guess
and its hidden hand pick are worded and flagged without changing their fingerprints;
the compound caches translate pick lists into correct action sequences; the
set-cards legal sets are independent between players."""

from __future__ import annotations

import random

from engine_alpha.actions import (
    BINARY, SELECT_CARD, SELECT_IDENTITY, SELECT_NUMBER,
    P_EFFECT_NUMBER, P_EFFECT_TARGET, P_INITIAL_CARD, P_MULLIGAN, P_NAME_GUESS,
    P_SET_SLOT_A, P_SET_SLOT_B, select_card, select_identity, select_number,
)
from engine_alpha.cards import EFFECT_T, KEY_TO_INDEX, NUM_CARDS
from engine_alpha.effects import interpreter
from engine_alpha.game import Game
from engine_alpha.state import PH_INITIAL_SET, PH_SET_CARDS
from zutomayo.match.decisions import (
    KIND_BINARY_CHOICE, KIND_CARD_CHOICE, KIND_CARD_MULTI_CHOICE,
    KIND_IDENTITY_INPUT, KIND_NUMBER_CHOICE, MatchDecisionRequest, request_fingerprint,
)
from zutomayo.match.discord_adapter import PendingSelection, DiscordMatchDecisionAdapter
from zutomayo.match.presentation import (
    build_match_request, maximum_cards_to_set, name_guess_attack_bonus,
)
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


# -- name guess (03-047 family) ------------------------------------------------


def _game_past_setup(seed: int = 5) -> Game:
    game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    while game.state.turn == 0:
        game.apply(game.legal_actions()[-1])
    return game


def _set_hand(game: Game, player_index: int, card_keys: list[str]) -> None:
    game.state.players[player_index].hand[:] = [
        game.state.new_instance(KEY_TO_INDEX[key]) for key in card_keys]


def _start_effect(game: Game, owner: int, card_key: str):
    """Start the effect of a fresh `card_key` for `owner`; returns its first request."""
    state = game.state
    source = state.new_instance(KEY_TO_INDEX[card_key])
    interpreter.start_effect(state, owner, source, EFFECT_T[state.inst_def[source]])
    return interpreter.resume(state, None, None)


def test_prompts_without_an_asking_effect_have_no_prefix_bonus_or_flag():
    class GameStub:
        class state:
            acting = 0
            frame_stack = []

    request = build_match_request(GameStub, select_identity(P_NAME_GUESS, [0, 1, 2]))
    assert request.prompt_text == (
        'name a card [カード名を1つ指定]. The full name must match, including the part in '
        'parentheses; any printing counts.')

    request = build_match_request(GameStub, select_number(P_EFFECT_NUMBER, 1, 3))
    assert request.hidden_hand_pick is False
    assert request.prompt_text == 'select a number.'


def test_name_guess_attack_bonus_is_read_from_the_catalog():
    assert [name_guess_attack_bonus(effect_id)
            for effect_id in ('03-047', '03-059', '03-094', '03-105')] == [50, 100, 40, 100]
    assert name_guess_attack_bonus('01-006') is None
    assert name_guess_attack_bonus('04-061') is None
    assert name_guess_attack_bonus('') is None


def test_name_guess_keeps_its_kind_and_every_definition():
    game = _game_past_setup()
    _set_hand(game, 1, ['01-001', '04-001', '03-047'])
    engine_request = _start_effect(game, 0, '03-047')
    assert engine_request.purpose == P_NAME_GUESS

    request = build_match_request(game, engine_request, opponent_name='Beta')
    assert request.kind == KIND_IDENTITY_INPUT
    assert request.action_count() == NUM_CARDS == 425
    assert request_fingerprint(request) == {
        'kind': 'identity_input', 'purpose': P_NAME_GUESS, 'player_index': 0, 'action_count': 425,
    }
    assert request.hidden_hand_pick is False
    assert request.prompt_text == (
        '**NIRA-CHAN (Humanoid)** [にらちゃん（ヒューマノイド）] effect: name a card '
        '[カード名を1つ指定]. The full name must match, including the part in parentheses; '
        'any printing counts. Attack +50 on a match.')


def test_name_guess_hand_pick_is_flagged_and_worded_as_face_down_cards():
    game = _game_past_setup()
    _set_hand(game, 1, ['01-001', '04-001', '03-047'])
    name_request = _start_effect(game, 0, '03-047')
    pick_request = interpreter.resume(game.state, name_request, KEY_TO_INDEX['01-022'])
    assert pick_request.purpose == P_EFFECT_NUMBER

    request = build_match_request(game, pick_request, opponent_name='Beta')
    assert request.kind == KIND_NUMBER_CHOICE
    assert request.hidden_hand_pick is True
    assert (request.minimum_value, request.maximum_value) == (1, 3)
    assert request.prompt_text == (
        "**NIRA-CHAN (Humanoid)** [にらちゃん（ヒューマノイド）] effect: choose one of "
        "Beta's 3 face-down cards to reveal.")
    assert request_fingerprint(request) == {
        'kind': KIND_NUMBER_CHOICE, 'purpose': P_EFFECT_NUMBER, 'player_index': 0, 'action_count': 3,
    }


def test_hand_pick_of_a_single_card_reads_naturally():
    game = _game_past_setup()
    _set_hand(game, 1, ['01-001'])
    name_request = _start_effect(game, 0, '03-094')
    pick_request = interpreter.resume(game.state, name_request, KEY_TO_INDEX['01-001'])

    request = build_match_request(game, pick_request, opponent_name='Beta')
    assert request.hidden_hand_pick is True
    assert request.prompt_text == "**Shoot** [撃つ] effect: choose Beta's only face-down card to reveal."


def test_name_guess_borrowed_via_01_006_is_flagged_with_its_own_bonus():
    """01-006 resolves 03-059 from the abyss as a nested frame whose source is the
    borrowed card, so the prompts name 03-059 and its +100."""
    game = _game_past_setup()
    state = game.state
    _set_hand(game, 1, ['01-001', '03-047'])
    borrowed = state.new_instance(KEY_TO_INDEX['03-059'])
    state.players[0].abyss[:] = [borrowed]

    target_request = _start_effect(game, 0, '01-006')
    assert target_request.purpose == P_EFFECT_TARGET
    name_request = interpreter.resume(
        state, target_request, list(target_request.candidates).index(borrowed))
    request = build_match_request(game, name_request, opponent_name='Beta')
    assert request.kind == KIND_IDENTITY_INPUT
    assert request.prompt_text.startswith('**Playing make-believe** [ごっこ遊び] effect: name a card')
    assert request.prompt_text.endswith('Attack +100 on a match.')

    pick_request = interpreter.resume(state, name_request, KEY_TO_INDEX['01-001'])
    request = build_match_request(game, pick_request, opponent_name='Beta')
    assert request.hidden_hand_pick is True
    assert "choose one of Beta's 2 face-down cards to reveal." in request.prompt_text


def test_other_number_picks_are_not_flagged():
    game = _game_past_setup()
    _set_hand(game, 0, ['01-001', '04-001'])
    engine_request = _start_effect(game, 0, '04-061')
    assert engine_request.purpose == P_EFFECT_NUMBER

    request = build_match_request(game, engine_request, opponent_name='Beta')
    assert request.hidden_hand_pick is False
    assert request.prompt_text.endswith('effect: select a number.')


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


def test_initial_card_cache_picks_the_chosen_card():
    adapter = DiscordMatchDecisionAdapter(transport=None)
    selection = _selection_with([31])

    request = _request_for(P_INITIAL_CARD, [30, 31, 32])
    assert adapter._compound_action(selection, request) == 1

    stale = _selection_with([99])
    assert adapter._compound_action(stale, request) is None


def test_initial_card_legal_sets_are_independent_between_players():
    """Same property as set-cards, for the opening placement: the second
    mover's candidates are their own untouched hand, so both prompts can be
    presented at once."""
    for seed in range(6):
        decks = random_full_pool_decks(400 + seed)
        game = Game(seed=seed, mode='fixed_decks', decks=decks)
        rng = random.Random(seed)
        steps = 0
        while not game.is_terminal() and steps < 600:
            state = game.state
            if state.phase == PH_INITIAL_SET and state.pending is not None:
                first_mover = state.acting
                second_mover = 1 - first_mover
                second_hand_before = list(state.players[second_mover].hand)

                probe = game.clone()
                probe.apply(rng.choice(probe.legal_actions()))
                assert probe.state.phase == PH_INITIAL_SET
                assert probe.state.acting == second_mover
                assert list(probe.state.pending.candidates) == second_hand_before
                break
            game.apply(rng.choice(game.legal_actions()))
            steps += 1


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
