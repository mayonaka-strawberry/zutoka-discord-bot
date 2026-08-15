"""The Discord adapter's compound prompts: the opening placement is presented
to both players at once, and the set-cards prompt is preceded by the last
turn's result and the player's hand."""

from __future__ import annotations

import asyncio
import random

from engine_alpha.actions import P_SET_SLOT_A
from engine_alpha.game import Game
from engine_alpha.state import PH_INITIAL_SET, PH_SET_CARDS
from zutomayo.match.discord_adapter import (
    FAMILY_INITIAL_CARD, FAMILY_SET_CARDS, DiscordMatchDecisionAdapter,
)
from zutomayo.match.presentation import build_match_request
from tests.match.support import FakeSession, RecordingTransport, random_full_pool_decks


class _RecordingBroker:
    def __init__(self) -> None:
        self.submissions: list[tuple] = []

    def submit(self, sequence_number: int, payload_type: str, payload) -> None:
        self.submissions.append((sequence_number, payload_type, payload))


def _runtime(game: Game):
    session = FakeSession()
    session.transport = RecordingTransport()
    session.broker = _RecordingBroker()
    session.game = game
    adapter = DiscordMatchDecisionAdapter(session.transport)
    return session, adapter


def _play_until(seed: int, phase: int) -> Game:
    game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    rng = random.Random(seed)
    for _ in range(600):
        if game.state.phase == phase and game.state.pending is not None:
            return game
        game.apply(rng.choice(game.legal_actions()))
    raise AssertionError(f'never reached phase {phase}')


def _present(session, adapter, game: Game, sequence_number: int = 0):
    request = build_match_request(game, game.decision_context(), opponent_name='opp')
    request.sequence_number = sequence_number
    asyncio.run(adapter.present_decision(session, request))
    return request


def test_initial_placement_prompts_both_players_before_either_answers():
    game = _play_until(7, PH_INITIAL_SET)
    first_mover = game.state.acting
    session, adapter = _runtime(game)

    _present(session, adapter, game)

    transport = session.transport
    assert transport.player_messages[0], 'player 0 was not prompted'
    assert transport.player_messages[1], 'player 1 was not prompted'
    assert transport.channel_messages == [], 'a placement prompt must stay private'
    assert session.broker.submissions == [], 'nothing is answered until a player picks'
    for index in (0, 1):
        prompt = transport.player_messages[index][-1]
        assert 'Battle Zone' in prompt['content']
        assert prompt['view'] is not None


def test_initial_placement_answer_maps_to_the_engine_action():
    game = _play_until(7, PH_INITIAL_SET)
    acting = game.state.acting
    candidates = list(game.state.pending.candidates)
    session, adapter = _runtime(game)
    _present(session, adapter, game, sequence_number=4)

    # What the view's submit callback does once the player confirms.
    adapter.pending_selections[(acting, FAMILY_INITIAL_CARD)].resolve([candidates[2]])

    assert session.broker.submissions == [(4, 'action', 2)]


def test_set_cards_prompt_leads_with_the_last_result_and_the_hand(monkeypatch):
    from zutomayo.ui import embeds

    async def fake_hand_image(hand):
        return f'hand-image({len(hand)})'

    monkeypatch.setattr(embeds, 'create_hand_image_off_thread', fake_hand_image)

    game = _play_until(7, PH_SET_CARDS)
    acting = game.state.acting
    assert game.state.pending.purpose == P_SET_SLOT_A
    session, adapter = _runtime(game)

    _present(session, adapter, game)

    messages = session.transport.player_messages[acting]
    assert messages[0]['content'].endswith('card') or messages[0]['content'].endswith('cards')
    assert 'Set' in messages[0]['content']
    assert messages[0]['embed'].title == 'Your Hand'
    assert messages[1]['files'] == [f'hand-image({len(game.state.players[acting].hand)})']
    assert messages[2]['view'] is not None
    assert (acting, FAMILY_SET_CARDS) in adapter.pending_selections


def test_set_cards_status_line_names_the_previous_result():
    game = _play_until(7, PH_SET_CARDS)
    acting = game.state.acting
    session, adapter = _runtime(game)

    async def briefing(maximum):
        session.transport.player_messages[acting].clear()
        await adapter._send_hand_briefing(session, acting, [], maximum)
        return session.transport.player_messages[acting][0]['content']

    game.state.last_battle_winner = acting
    assert asyncio.run(briefing(1)) == 'Last Turn Result: WIN | Set 1 card'
    game.state.last_battle_winner = 1 - acting
    assert asyncio.run(briefing(2)) == 'Last Turn Result: LOSE | Set up to 2 cards'
    game.state.last_battle_winner = -1
    assert asyncio.run(briefing(1)) == 'Last Turn Result: DRAW | Set 1 card'


def test_prompts_are_not_sent_to_a_seat_that_takes_no_dms():
    game = _play_until(7, PH_INITIAL_SET)
    session, adapter = _runtime(game)
    session.transport.muted = True  # delivers_to_player is False for both seats

    _present(session, adapter, game)

    assert session.transport.player_messages == {0: [], 1: []}


def test_set_slot_a_offers_no_pass_and_cannot_submit_an_empty_selection():
    """Ground Rules 5.2.1.5 / Q&A No.4: a player holding cards must set at least one.

    The single-slot view used to hardcode a 'Set nothing' button regardless of
    `engine_request.allow_pass`. Pressing it was dropped by the broker as an
    illegal action, the prompt then stalled for the full timeout, and the
    fallback set the player's first hand card anyway -- three of those in a row
    forfeited the match. There was never an exception; the symptom was a dead
    button and a stall, which is what this pins.
    """
    game = _play_until(7, PH_SET_CARDS)
    acting = game.state.acting
    engine_request = game.decision_context()
    assert engine_request.purpose == P_SET_SLOT_A
    assert game.state.players[acting].hand, 'precondition: the player holds cards'
    assert engine_request.allow_pass is False, 'the engine forbids setting zero'

    session, adapter = _runtime(game)
    request = _present(session, adapter, game, sequence_number=11)

    view = session.transport.player_messages[acting][-1]['view']
    assert getattr(view, 'allow_pass', False) is False, 'no "set nothing" button'

    # An empty pick can no longer be translated into a PASS the engine rejects.
    selection = adapter.pending_selections[(acting, FAMILY_SET_CARDS)]
    selection.chosen = []
    assert adapter._compound_action(selection, request) is None

    # And a real pick still maps to the right engine action.
    candidates = list(engine_request.candidates)
    selection.chosen = [candidates[1]]
    assert adapter._compound_action(selection, request) == 1
