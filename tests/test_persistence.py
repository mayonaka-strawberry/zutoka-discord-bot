"""Unit tests for game persistence: manifest round-trip, decision log
append/load, torn-line tolerance, and directory deletion."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import zutomayo.engine.game_persistence as game_persistence_module  # noqa: E402
from zutomayo.engine.decisions import (  # noqa: E402
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TIMEOUT,
    DecisionOption,
    DecisionRequest,
    DecisionResponse,
    request_fingerprint,
)
from zutomayo.engine.game_persistence import (  # noqa: E402
    GamePersistence,
    load_decision_log,
    load_manifest,
)
from zutomayo.engine.game_session import GameSession  # noqa: E402


def _point_active_games_at(tmp_path: Path) -> Path:
    directory = tmp_path / 'active_games'
    game_persistence_module.ACTIVE_GAMES_DIRECTORY = directory
    return directory


def _make_session() -> GameSession:
    session = GameSession(game_id='persist-test', channel_id=42, creator_id=111)
    session.add_player(222)
    session.random_seed = 987654321
    return session


def test_manifest_round_trip(tmp_path):
    _point_active_games_at(tmp_path)
    session = _make_session()
    session.player_deck_names = {0: 'My Deck', 1: None}

    persistence = GamePersistence.create_for_session(session, 'standard', extra_fields={
        'deck_0': [[1, 5], [2, 17]],
        'deck_1': [[3, 8], [4, 2]],
    })
    manifest = load_manifest(persistence.game_directory)

    assert manifest['game_id'] == 'persist-test'
    assert manifest['channel_id'] == 42
    assert manifest['mode'] == 'standard'
    assert manifest['player_discord_ids'] == [[111, 0], [222, 1]]
    assert manifest['player_deck_names'] == {'0': 'My Deck', '1': None}
    assert manifest['random_seed'] == 987654321
    assert manifest['deck_0'] == [[1, 5], [2, 17]]
    assert manifest['deck_1'] == [[3, 8], [4, 2]]


def test_decision_log_append_and_load(tmp_path):
    _point_active_games_at(tmp_path)
    session = _make_session()
    persistence = GamePersistence.create_for_session(session, 'standard')

    request_a = DecisionRequest(
        kind=KIND_EFFECT_CARD_SELECT, player_index=0, prompt_text='pick',
        options=[DecisionOption('01-001', 'A', 0), DecisionOption('01-002', 'B', 1)],
    )
    request_a.sequence_number = 0
    request_b = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=1, prompt_text='number',
        minimum_value=0, maximum_value=5,
    )
    request_b.sequence_number = 1

    async def append_all():
        await persistence.append_decision(request_a, DecisionResponse(0, PAYLOAD_INDICES, [1]))
        await persistence.append_decision(request_b, DecisionResponse(1, PAYLOAD_TIMEOUT, None))

    asyncio.run(append_all())

    replay_log = load_decision_log(persistence.game_directory)
    assert set(replay_log.keys()) == {0, 1}
    fingerprint_a, response_a = replay_log[0]
    assert fingerprint_a == request_fingerprint(request_a)
    assert response_a.payload_type == PAYLOAD_INDICES
    assert response_a.payload == [1]
    fingerprint_b, response_b = replay_log[1]
    assert response_b.payload_type == PAYLOAD_TIMEOUT
    assert response_b.payload is None


def test_torn_final_line_is_dropped(tmp_path):
    _point_active_games_at(tmp_path)
    session = _make_session()
    persistence = GamePersistence.create_for_session(session, 'standard')

    request = DecisionRequest(
        kind=KIND_EFFECT_NUMBER_SELECT, player_index=0, prompt_text='number',
        minimum_value=0, maximum_value=3,
    )
    request.sequence_number = 0
    asyncio.run(persistence.append_decision(request, DecisionResponse(0, PAYLOAD_NUMBER, 2)))

    decisions_path = persistence.game_directory / 'decisions.jsonl'
    with open(decisions_path, 'a', encoding='utf-8', newline='\n') as decisions_file:
        decisions_file.write('{"sequence_number": 1, "payload_ty')  # torn mid-crash

    replay_log = load_decision_log(persistence.game_directory)
    assert set(replay_log.keys()) == {0}
    assert replay_log[0][1].payload == 2


def test_delete_removes_directory(tmp_path):
    _point_active_games_at(tmp_path)
    session = _make_session()
    persistence = GamePersistence.create_for_session(session, 'standard')
    assert persistence.game_directory.exists()
    persistence.delete()
    assert not persistence.game_directory.exists()
