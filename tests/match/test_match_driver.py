"""End-to-end driver tests: full games over the broker with scripted
adapters, decision logging, narration events, and replay to identical state."""

from __future__ import annotations

import asyncio

from engine_alpha.game import Game
from zutomayo.match.broker import MatchDecisionBroker
from zutomayo.match.match_driver import EngineMatchDriver
from zutomayo.match.narrator import MatchNarrator
from zutomayo.match.persistence import (
    card_keys_for_definition_indices,
    definition_indices_for_card_keys,
)
from zutomayo.match.resume import rebuild_game_from_manifest
from tests.match.support import (
    FakeSession,
    MemoryRecordStore,
    RecordingTransport,
    ScriptedActionAdapter,
    random_full_pool_decks,
    state_digest,
)

PLAYER_NAMES = {0: 'Player 1', 1: 'Player 2'}


def build_runtime(seed: int, decks, replay_log=None):
    session = FakeSession(game_id=f'TEST-{seed:05d}')
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = ScriptedActionAdapter(lambda: session.broker, seed=seed)
    broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    session.broker = broker
    session.transport = transport
    session.persistence = store
    session.game = Game(seed=seed, mode='fixed_decks', decks=decks)
    if replay_log is not None:
        broker.replay_log = replay_log
        broker.replaying = True
        transport.muted = True

        async def go_live():
            transport.muted = False

        broker.on_go_live = go_live
    narrator = MatchNarrator(session, transport)
    driver = EngineMatchDriver(session, session.game, broker, narrator, PLAYER_NAMES)
    return session, driver, store, transport


def test_full_game_completes_and_records():
    decks = random_full_pool_decks(7)
    session, driver, store, transport = build_runtime(7, decks)

    outcome = asyncio.run(driver.run_to_completion())

    assert outcome.winner in (0, 1, 2)
    assert outcome.forfeited_player is None
    assert session.game.is_terminal()
    assert store.decisions, 'every game must log decisions'
    sequence_numbers = [record['sequence_number'] for record in store.decisions]
    assert sequence_numbers == list(range(len(sequence_numbers)))
    event_types = {event['event_type'] for event in store.events}
    assert 'phase_entered' in event_types
    assert 'battle_result' in event_types
    assert 'decision_made' in event_types
    assert 'game_end' in event_types
    assert 'state_snapshot' in event_types


def test_replay_reproduces_identical_state():
    decks = random_full_pool_decks(11)
    session, driver, store, transport = build_runtime(11, decks)
    outcome = asyncio.run(driver.run_to_completion())
    original_digest = state_digest(session.game)

    for truncation in (0, 2, len(store.decisions) // 2, len(store.decisions)):
        full_log = store.replay_log()
        truncated_log = {k: v for k, v in full_log.items() if k < truncation}
        replay_session, replay_driver, replay_store, replay_transport = build_runtime(
            11, decks, replay_log=truncated_log)
        replay_outcome = asyncio.run(replay_driver.run_to_completion())
        assert replay_outcome.winner == outcome.winner
        assert state_digest(replay_session.game) == original_digest


def test_manifest_round_trip_rebuilds_the_same_game():
    decks = random_full_pool_decks(13)
    keys = {
        0: card_keys_for_definition_indices(decks[0]),
        1: card_keys_for_definition_indices(decks[1]),
    }
    assert definition_indices_for_card_keys(keys[0]) == decks[0]
    assert definition_indices_for_card_keys(keys[1]) == decks[1]

    manifest = {
        'random_seed': 13,
        'deck_0': keys[0],
        'deck_1': keys[1],
    }
    rebuilt = rebuild_game_from_manifest(manifest)
    fresh = Game(seed=13, mode='fixed_decks', decks=decks)
    assert state_digest(rebuilt) == state_digest(fresh)


def test_replay_suppresses_transport_and_events():
    decks = random_full_pool_decks(17)
    session, driver, store, transport = build_runtime(17, decks)
    asyncio.run(driver.run_to_completion())
    live_channel_message_count = len(transport.channel_messages)
    assert live_channel_message_count > 0

    replay_session, replay_driver, replay_store, replay_transport = build_runtime(
        17, decks, replay_log=store.replay_log())
    asyncio.run(replay_driver.run_to_completion())
    assert replay_transport.channel_messages == []
    replayed_live_events = [e for e in replay_store.events]
    assert replayed_live_events == []
