"""Series-flow logic (pure parts) and summary rendering of v2 event streams."""

from __future__ import annotations

import asyncio

from engine_alpha.rng import derive_seed
from zutomayo.match.decisions import (
    KIND_SIDE_CHOICE,
    SIDE_ACTION_DAY,
    SIDE_ACTION_NIGHT,
)
from zutomayo.match.series_flow import TcgSeriesFlow


class CardStub:
    def __init__(self, pack: int, card_id: int) -> None:
        self.pack = pack
        self.id = card_id


def test_match_seed_derivation_is_deterministic_and_distinct():
    seeds_first = [derive_seed(123456789, counter) for counter in range(1, 8)]
    seeds_second = [derive_seed(123456789, counter) for counter in range(1, 8)]
    assert seeds_first == seeds_second
    assert len(set(seeds_first)) == len(seeds_first)
    assert seeds_first != [derive_seed(987654321, counter) for counter in range(1, 8)]


def test_cards_for_keys_honors_duplicates():
    pool = [CardStub(1, 5), CardStub(1, 5), CardStub(2, 10)]
    chosen = TcgSeriesFlow._cards_for_keys(pool, [[1, 5], [1, 5]])
    assert len(chosen) == 2
    assert chosen[0] is pool[0]
    assert chosen[1] is pool[1]
    only_one = TcgSeriesFlow._cards_for_keys(pool, [[1, 5], [9, 99]])
    assert len(only_one) == 1


def test_switch_application_moves_cards_between_deck_and_side():
    deck = [CardStub(1, 1), CardStub(1, 2), CardStub(1, 3)]
    side = [CardStub(2, 1), CardStub(2, 2)]
    removed_keys = [[1, 2]]
    added_keys = [[2, 1]]

    for card in TcgSeriesFlow._cards_for_keys(deck, removed_keys):
        deck.remove(card)
        side.append(card)
    added = TcgSeriesFlow._cards_for_keys(side, added_keys)
    for card in added:
        side.remove(card)
        deck.append(card)

    assert [(c.pack, c.id) for c in deck] == [(1, 1), (1, 3), (2, 1)]
    assert sorted((c.pack, c.id) for c in side) == [(1, 2), (2, 2)]


def test_night_player_for_choice_maps_both_players_and_actions():
    for chooser_index in (0, 1):
        assert TcgSeriesFlow._night_player_for_choice(
            chooser_index, SIDE_ACTION_NIGHT) == chooser_index
        assert TcgSeriesFlow._night_player_for_choice(
            chooser_index, SIDE_ACTION_DAY) == 1 - chooser_index


def _run_scripted_series(
    match_winners: list[int],
    best_of: int = 3,
    seed: int = 5,
    replay_log: dict | None = None,
) -> tuple[list, object, object]:
    """Drive run_tcg with stubbed matches so only the series loop runs.

    Returns the night_player passed to each match, the record store, and the
    transport. Real matches, the side-deck switch, and Elo recording are
    stubbed out: this exercises the side-choice sequencing and its persistence,
    which the stubs would otherwise bury under image rendering and DB writes.
    """
    from zutomayo.match.broker import MatchDecisionBroker
    from zutomayo.match.match_driver import MatchOutcome
    from tests.match.support import (
        FakeSession,
        MemoryRecordStore,
        RecordingTransport,
        ScriptedActionAdapter,
    )

    session = FakeSession(game_id=f'SERIES-{seed:05d}')
    session.is_tcg = True
    session.best_of = best_of
    session.random_seed = 123456789
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = ScriptedActionAdapter(lambda: session.broker, seed=seed)
    broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    if replay_log is not None:
        broker.replay_log = replay_log
        broker.replaying = True
    session.broker = broker
    session.transport = transport
    session.persistence = store

    flow = TcgSeriesFlow(bot=None, best_of=best_of)
    night_players_seen: list = []
    remaining_winners = list(match_winners)

    async def stub_run_single_match(session, deck_0, deck_1, *, record_store=None,
                                    engine_seed=None, night_player=None):
        night_players_seen.append(night_player)
        return MatchOutcome(winner=remaining_winners.pop(0), forfeited_player=None)

    async def stub_switch_cards(session, names, deck_0, side_0, deck_1, side_1):
        return deck_0, side_0, deck_1, side_1

    async def stub_record_series_stats(session, wins):
        return None

    flow.match_flow.run_single_match = stub_run_single_match
    flow._do_switch_cards = stub_switch_cards
    flow._record_series_stats = stub_record_series_stats

    asyncio.run(flow.run_tcg(session, resumed_decks=([], [], [], [])))
    assert not remaining_winners, 'the series ended before every scripted match ran'
    return night_players_seen, store, transport


def test_first_match_flips_and_later_matches_use_the_losers_choice():
    night_players, store, transport = _run_scripted_series([0, 1, 0])

    # Match 1 leaves the flip to the engine; matches 2 and 3 are chosen.
    assert night_players[0] is None
    assert night_players[1] is not None
    assert night_players[2] is not None

    side_choices = [event for event in store.events if event['event_type'] == 'side_choice']
    assert len(side_choices) == 2
    # Player 1 lost match 1, player 0 lost match 2.
    assert [event['payload']['chooser_index'] for event in side_choices] == [1, 0]
    for event, chosen_night_player in zip(side_choices, night_players[1:]):
        payload = event['payload']
        assert payload['night_player'] == chosen_night_player
        assert payload['chose_night'] == (payload['night_player'] == payload['chooser_index'])

    logged_kinds = [record['fingerprint']['kind'] for record in store.decisions]
    assert logged_kinds.count(KIND_SIDE_CHOICE) == 2

    announcements = [message.get('content', '') for message in transport.channel_messages]
    assert any('chose to play' in text for text in announcements)


def test_draw_replays_with_the_same_chooser():
    # Match 1 draws (winner 2), replays, then plays out normally.
    night_players, store, _ = _run_scripted_series([2, 0, 1, 0])

    # The drawn match 1 and its replay both predate any chooser.
    assert night_players[0] is None
    assert night_players[1] is None
    assert night_players[2] is not None

    side_choices = [event for event in store.events if event['event_type'] == 'side_choice']
    assert [event['payload']['chooser_index'] for event in side_choices] == [1, 0]


def test_replaying_a_series_reproduces_the_same_sides():
    live_night_players, store, _ = _run_scripted_series([0, 1, 0])
    replayed_night_players, replayed_store, _ = _run_scripted_series(
        [0, 1, 0], replay_log=store.replay_log(),
    )

    assert replayed_night_players == live_night_players
    # Events are suppressed while replaying, so the log is the evidence.
    assert [record['payload'] for record in replayed_store.decisions] == []


def test_summary_renders_the_side_choice_between_matches():
    from zutomayo.match.decisions import SIDE_LABEL_DAY, SIDE_LABEL_NIGHT
    from zutomayo.ui.game_summary_view import build_game_summary

    game_row = {'game_id': 'SERIES-00001', 'status': 'completed', 'is_tcg': True, 'best_of': 3}
    player_names = {0: 'Alpha', 1: 'Beta'}
    events = [
        {'event_index': 0, 'event_type': 'match_result', 'match_number': 1,
         'payload': {'match_number': 1, 'winner_index': 0, 'series_score': [1, 0]}},
        {'event_index': 1, 'event_type': 'side_choice', 'match_number': 1,
         'payload': {'chooser_index': 1, 'night_player': 1,
                     'chose_night': True, 'timed_out': False}},
    ]

    summary = build_game_summary(game_row, player_names, events, card_index={})
    text = '\n'.join(page.description for page in summary.pages)
    assert f'**Beta** lost the match and chose {SIDE_LABEL_NIGHT}' in text
    assert f'**Alpha** plays {SIDE_LABEL_DAY}' in text


def test_summary_renders_v2_event_stream():
    from engine_alpha.game import Game
    from zutomayo.data.deck_validator import get_card_index
    from zutomayo.match.broker import MatchDecisionBroker
    from zutomayo.match.match_driver import EngineMatchDriver
    from zutomayo.match.narrator import MatchNarrator
    from zutomayo.ui.game_summary_view import (
        _describe_decision_line,
        _match_opening_lines,
        _turn_lines,
    )
    from tests.match.support import (
        FakeSession,
        MemoryRecordStore,
        RecordingTransport,
        ScriptedActionAdapter,
        random_full_pool_decks,
    )

    decks = random_full_pool_decks(21)
    session = FakeSession(game_id='SUMMARY-00021')
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = ScriptedActionAdapter(lambda: session.broker, seed=21)
    broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    session.broker = broker
    session.transport = transport
    session.persistence = store
    session.game = Game(seed=21, mode='fixed_decks', decks=decks)
    narrator = MatchNarrator(session, transport)
    driver = EngineMatchDriver(
        session, session.game, broker, narrator, {0: 'Alpha', 1: 'Beta'})
    asyncio.run(driver.run_to_completion())

    _, card_index = get_card_index()
    player_names = {0: 'Alpha', 1: 'Beta'}
    events = [
        {'event_type': event['event_type'], 'payload': event['payload']}
        for event in store.events
    ]

    turn_text = '\n'.join(_turn_lines(events, player_names, card_index))
    assert 'Battle:' in turn_text
    assert 'Game end' in turn_text

    decision_events = [e for e in events if e['event_type'] == 'decision_made']
    assert decision_events
    for event in decision_events:
        line = _describe_decision_line(event['payload'], player_names)
        assert line and '?' not in line.split(' — ')[0]

    opening_text = '\n'.join(_match_opening_lines(events, player_names, card_index))
    assert 'redraw' in opening_text or opening_text == ''
