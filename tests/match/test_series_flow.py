"""Series-flow logic (pure parts) and summary rendering of v2 event streams."""

from __future__ import annotations

import asyncio

from engine_alpha.rng import derive_seed
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
