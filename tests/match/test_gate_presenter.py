"""Phase-gate presentation: the sink captures the board at every phase
boundary, gates are posted in the pre-port order, zone strips are re-sent only
when they change, and nothing at all is posted between the two players'
card placements."""

from __future__ import annotations

import asyncio
import random
import re

from engine_alpha.actions import P_INITIAL_CARD, P_SET_SLOT_A, P_SET_SLOT_B
from engine_alpha.events import EVENT_PHASE_CHANGED
from engine_alpha.game import Game
from engine_alpha.state import PH_ADVANCE_CHRONOS, PH_REVEAL
from zutomayo.match.broker import MatchDecisionBroker
from zutomayo.match.gate_presenter import (
    ZONE_KEYS, GatePresenter, SnapshottingEventSink, zone_instance_ids,
)
from zutomayo.match.match_driver import EngineMatchDriver
from zutomayo.match.narrator import MatchNarrator
from zutomayo.match.state_view import project_board_view
from tests.match.support import (
    FakeSession,
    MemoryRecordStore,
    RecordingTransport,
    ScriptedActionAdapter,
    random_full_pool_decks,
)

PLAYER_NAMES = {0: 'Player 1', 1: 'Player 2'}
SET_PURPOSES = (P_INITIAL_CARD, P_SET_SLOT_A, P_SET_SLOT_B)


def channel_contents(transport: RecordingTransport) -> list[str]:
    return [
        message['content'] for message in transport.channel_messages
        if message.get('content')
    ]


def run_game(seed: int, wrap_adapter=None):
    session = FakeSession(game_id=f'GATE-{seed:05d}')
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = ScriptedActionAdapter(lambda: session.broker, seed=seed)
    if wrap_adapter is not None:
        adapter = wrap_adapter(adapter, transport)
    broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    session.broker = broker
    session.transport = transport
    session.persistence = store
    session.game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    narrator = MatchNarrator(session, transport)
    driver = EngineMatchDriver(session, session.game, broker, narrator, PLAYER_NAMES)
    asyncio.run(driver.run_to_completion())
    return session, store, transport


# -- the snapshotting sink ---------------------------------------------------


def test_set_cards_are_face_down_at_the_reveal_boundary():
    """The whole point of snapshotting mid-apply: at the moment the engine
    enters PH_REVEAL both players have committed and neither card is face up
    yet, which is the board the Set cards gate shows."""
    game = Game(seed=7, mode='fixed_decks', decks=random_full_pool_decks(7))
    sink = SnapshottingEventSink(lambda: project_board_view(game, PLAYER_NAMES))
    game.state.event_sink = sink
    rng = random.Random(3)

    face_down_seen = 0
    revealed_seen = 0
    for _ in range(400):
        if game.is_terminal():
            break
        sink.clear()
        game.apply(rng.choice(game.legal_actions()))
        for index, event in enumerate(list(sink)):
            if event[0] != EVENT_PHASE_CHANGED:
                continue
            assert index in sink.snapshots, 'every phase boundary must carry a snapshot'
            if event[1] not in (PH_REVEAL, PH_ADVANCE_CHRONOS):
                continue
            for player in sink.snapshots[index].players:
                for zone in (player.set_zone_a, player.set_zone_b):
                    if zone is None:
                        continue
                    if event[1] == PH_REVEAL:
                        assert not zone.face_up
                        face_down_seen += 1
                    elif sink.snapshots[index].turn > 1:
                        assert zone.face_up
                        revealed_seen += 1

    assert face_down_seen and revealed_seen


def test_clearing_the_sink_drops_its_snapshots():
    game = Game(seed=5, mode='fixed_decks', decks=random_full_pool_decks(5))
    sink = SnapshottingEventSink(lambda: project_board_view(game, PLAYER_NAMES))
    game.state.event_sink = sink
    game.apply(game.legal_actions()[0])
    sink.clear()
    assert list(sink) == []
    assert sink.snapshots == {}


# -- gate sequencing ---------------------------------------------------------


def test_turn_gates_follow_the_pre_port_order():
    _, _, transport = run_game(7)
    contents = channel_contents(transport)

    assert contents[0].startswith('**ゲームスタート: Player 1 vs. Player 2**')
    assert contents[1].startswith('**1 — Advance Chronos [時間を進める] (')
    assert contents[2].startswith('**1 — Character/Enchant/Area Enchant Effects')
    assert contents[3].startswith('**1 — Battle Damage Calculation')
    assert contents[4] == '**Turn 1 complete. Preparing next turn...**'

    expected = [
        r'\*\*2 — Set cards ',
        r'\*\*2 — Reveal set cards ',
        r'\*\*2 — Advance Chronos \[時間を進める\] \(\d+\)\*\*',
        r'\*\*2 — Character Swap ',
        r'\*\*2 — Area Enchant Swap ',
        r'\*\*2 — Character/Enchant/Area Enchant Effects ',
        r'\*\*2 — Battle Damage Calculation ',
        r'\*\*Turn 2 complete\.',
    ]
    turn_two = [text for text in contents if re.match(r'\*\*(2 —|Turn 2 )', text)]
    assert len(turn_two) == len(expected)
    for text, pattern in zip(turn_two, expected):
        assert re.match(pattern, text), f'{text!r} does not match {pattern!r}'


def test_every_battle_result_gets_exactly_one_gate():
    """Including the last one: a game that ends on battle damage never reaches
    another phase, so its gate has to be emitted from the game-over event."""
    for seed in range(6):
        _, store, transport = run_game(seed)
        battles = sum(
            1 for event in store.events if event['event_type'] == 'battle_result')
        gates = sum(
            1 for text in channel_contents(transport)
            if 'Battle Damage Calculation' in text)
        assert battles == gates, f'seed {seed}: {battles} battles, {gates} gates'


def test_replay_stays_silent_but_keeps_zone_bookkeeping():
    _, store, transport = run_game(11)
    assert channel_contents(transport)

    replay_session = FakeSession(game_id='GATE-REPLAY')
    replay_transport = RecordingTransport()
    replay_transport.muted = True
    replay_store = MemoryRecordStore(replay_session.game_id, replay_session)
    adapter = ScriptedActionAdapter(lambda: replay_session.broker, seed=11)
    broker = MatchDecisionBroker(replay_session, {0: adapter, 1: adapter}, replay_store)
    broker.replay_log = store.replay_log()
    broker.replaying = True
    replay_session.broker = broker
    replay_session.transport = replay_transport
    replay_session.persistence = replay_store
    replay_session.game = Game(
        seed=11, mode='fixed_decks', decks=random_full_pool_decks(11))
    narrator = MatchNarrator(replay_session, replay_transport)
    driver = EngineMatchDriver(
        replay_session, replay_session.game, broker, narrator, PLAYER_NAMES)
    asyncio.run(driver.run_to_completion())

    assert replay_transport.channel_messages == []
    assert narrator.gate_presenter.previous_zone_ids, (
        'zone snapshots must still track through a muted replay')


# -- no information leaks between the two placements -------------------------


class _RequestRecordingAdapter:
    """Wraps the scripted adapter, noting how much had been posted at the
    moment each decision was presented."""

    def __init__(self, inner, transport) -> None:
        self.inner = inner
        self.transport = transport
        self.records: list[tuple] = []

    async def present_decision(self, session, request) -> None:
        state = session.game.state
        self.records.append((
            request.purpose, state.phase, state.turn,
            len(self.transport.channel_messages),
        ))
        await self.inner.present_decision(session, request)


def test_nothing_is_posted_between_the_two_players_placements():
    """Both players commit before anything is shown - the placement prompts of
    one phase must all see an identical channel history."""
    for seed in range(4):
        recorded: list[_RequestRecordingAdapter] = []

        def wrap(inner, transport, recorded=recorded):
            recorded.append(_RequestRecordingAdapter(inner, transport))
            return recorded[-1]

        run_game(seed, wrap_adapter=wrap)

        groups: dict[tuple, set[int]] = {}
        for purpose, phase, turn, channel_count in recorded[0].records:
            if purpose not in SET_PURPOSES:
                continue
            groups.setdefault((phase, turn), set()).add(channel_count)
        assert groups, f'seed {seed} never reached a placement prompt'
        for key, counts in groups.items():
            assert len(counts) == 1, (
                f'seed {seed}: channel grew between placements in phase/turn {key}: {counts}')


# -- zone strips -------------------------------------------------------------


class _FakeZoneProvider:
    def __init__(self) -> None:
        self.calls: list[set] = []

    async def __call__(self, board_view, names, indices):
        self.calls.append(indices)
        labels = [
            f'{names[0]} Abyss', f'{names[0]} Power Charger',
            f'{names[1]} Abyss', f'{names[1]} Power Charger',
        ]
        return [
            (label, None) for index, label in enumerate(labels)
            if indices is None or index in indices
        ]


def _board_views_with_different_zones():
    game = Game(seed=9, mode='fixed_decks', decks=random_full_pool_decks(9))
    rng = random.Random(9)
    first = None
    for _ in range(400):
        if game.is_terminal():
            break
        game.apply(rng.choice(game.legal_actions()))
        view = project_board_view(game, PLAYER_NAMES)
        if first is None and view.players[0].abyss:
            first = view
        elif first is not None and len(view.players[0].abyss) != len(first.players[0].abyss):
            return first, view
    raise AssertionError('no two board views with differing abyss contents')


def test_zone_strips_are_resent_only_when_they_change():
    before, after = _board_views_with_different_zones()
    session = FakeSession()
    transport = RecordingTransport()
    session.transport = transport
    provider = _FakeZoneProvider()
    presenter = GatePresenter(
        session, transport, zone_messages_provider=provider, gate_delay_seconds=0)

    asyncio.run(presenter._emit_gate('first', before, force_zones=True))
    assert provider.calls[0] is None, 'a forced gate renders every zone'

    provider.calls.clear()
    transport.channel_messages.clear()
    asyncio.run(presenter._emit_gate('unchanged', before))
    assert provider.calls == [], 'an unchanged board sends no zone messages'

    before_ids = zone_instance_ids(before)
    after_ids = zone_instance_ids(after)
    expected_changed = {
        index for index, key in enumerate(ZONE_KEYS)
        if before_ids[key] != after_ids[key]
    }
    assert expected_changed, 'the two board views must differ in at least one zone'
    assert expected_changed != set(range(len(ZONE_KEYS))), (
        'and must leave at least one zone alone, or the test proves nothing')

    provider.calls.clear()
    asyncio.run(presenter._emit_gate('changed', after))
    # Once per destination: the channel and both players.
    assert provider.calls == [expected_changed] * 3


def test_empty_zones_are_announced_as_empty():
    game = Game(seed=4, mode='fixed_decks', decks=random_full_pool_decks(4))
    board_view = project_board_view(game, PLAYER_NAMES)
    session = FakeSession()
    transport = RecordingTransport()
    session.transport = transport
    presenter = GatePresenter(
        session, transport, zone_messages_provider=_FakeZoneProvider(),
        gate_delay_seconds=0)

    asyncio.run(presenter._emit_gate('start', board_view, force_zones=True))
    contents = channel_contents(transport)
    assert 'Player 1 Abyss Empty' in contents
    assert 'Player 2 Power Charger Empty' in contents


# -- pacing ------------------------------------------------------------------


def test_gate_delay_is_skipped_when_the_transport_suppresses_it(monkeypatch):
    game = Game(seed=4, mode='fixed_decks', decks=random_full_pool_decks(4))
    board_view = project_board_view(game, PLAYER_NAMES)
    session = FakeSession()
    transport = RecordingTransport()
    session.transport = transport
    presenter = GatePresenter(session, transport)

    slept: list[float] = []

    async def record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, 'sleep', record_sleep)

    asyncio.run(presenter._emit_gate('paced', board_view))
    assert slept == []

    transport.suppress_phase_delays = False
    asyncio.run(presenter._emit_gate('paced', board_view))
    assert slept == [presenter.gate_delay_seconds]
