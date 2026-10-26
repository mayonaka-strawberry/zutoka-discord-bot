"""Each solo model plays a random deck from its own ranker file. Re-ranks change the
tracked files, so no test pins a deck count; routing uses temporary files, since the
tracked files can share decks.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import zutomayo.match.solo_flow as solo_flow_module
from engine_alpha.draft import validate_deck
from zutomayo.data.deck_storage import resolve_deck_cards
from zutomayo.data.deck_validator import get_card_index
from zutomayo.match.agents import (
    SOLO_OPPONENT_ALPHA_ZERO,
    SOLO_OPPONENT_DECK_FILES,
    SOLO_OPPONENT_PPO,
    load_solo_opponent_deck,
)
from zutomayo.match.match_flow import SingleMatchFlow, definition_indices_for_cards
from tests.match.support import FakeSession, RecordingTransport

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOLO_OPPONENTS = [SOLO_OPPONENT_ALPHA_ZERO, SOLO_OPPONENT_PPO]
HUMAN_DECK_SENTINEL = object()


def catalog_deck(first_card: int) -> list[dict]:
    """Two copies each of ten consecutive catalog cards, as card references."""
    _, card_index = get_card_index()
    card_keys = sorted(card_index)[first_card:first_card + 10]
    return [{'pack': pack, 'id': card_id} for pack, card_id in card_keys for _ in range(2)]


def write_deck_file(path: Path, decks: list[list[dict]]) -> Path:
    path.write_text(json.dumps({'decks': [{'cards': cards} for cards in decks]}), encoding='utf-8')
    return path


def install_deck_files(monkeypatch, tmp_path) -> dict[str, list[dict]]:
    """Point each opponent at a temporary file holding one deck no other opponent has."""
    decks = {}
    for position, opponent in enumerate(SOLO_OPPONENTS):
        decks[opponent] = catalog_deck(10 * position)
        deck_file = write_deck_file(tmp_path / f'{opponent}.json', [decks[opponent]])
        monkeypatch.setitem(SOLO_OPPONENT_DECK_FILES, opponent, deck_file)
    return decks


def card_references(cards) -> list[dict]:
    return [{'pack': card.pack, 'id': card.id} for card in cards]


def run_stubbed_solo_game(monkeypatch, opponent: str) -> tuple[FakeSession, list]:
    """run_solo_game with the model adapter, deck building and match stubbed out."""
    events: list = []

    async def fake_deck_building(flow, session):
        events.append('deck building')
        return HUMAN_DECK_SENTINEL

    async def fake_run_single_match(self, session, deck_1_cards, deck_2_cards, **options):
        events.append(('match', deck_1_cards, card_references(deck_2_cards)))

    monkeypatch.setattr(solo_flow_module, 'create_model_adapter', lambda session, opponent: object())
    monkeypatch.setattr(solo_flow_module, '_do_solo_deck_building', fake_deck_building)
    monkeypatch.setattr(SingleMatchFlow, 'run_single_match', fake_run_single_match)
    session = FakeSession()
    session.is_solo = True
    session.transport = RecordingTransport()
    asyncio.run(solo_flow_module.run_solo_game(None, session, opponent))
    return session, events


def test_each_model_reads_its_own_tracked_file():
    assert SOLO_OPPONENT_DECK_FILES == {
        SOLO_OPPONENT_ALPHA_ZERO: REPOSITORY_ROOT / 'data' / 'best_decks_alpha_zero.json',
        SOLO_OPPONENT_PPO: REPOSITORY_ROOT / 'data' / 'best_decks_ppo.json',
    }


def test_each_model_draws_only_from_its_own_file(monkeypatch, tmp_path):
    decks = install_deck_files(monkeypatch, tmp_path)
    _, card_index = get_card_index()

    for opponent in SOLO_OPPONENTS:
        assert card_references(load_solo_opponent_deck(opponent, card_index)) == decks[opponent]


def test_a_rewritten_file_is_used_by_the_next_game(monkeypatch, tmp_path):
    deck_file = write_deck_file(tmp_path / 'ranked.json', [catalog_deck(0)])
    monkeypatch.setitem(SOLO_OPPONENT_DECK_FILES, SOLO_OPPONENT_PPO, deck_file)
    _, card_index = get_card_index()
    load_solo_opponent_deck(SOLO_OPPONENT_PPO, card_index)

    write_deck_file(deck_file, [catalog_deck(10)])

    assert card_references(load_solo_opponent_deck(SOLO_OPPONENT_PPO, card_index)) == catalog_deck(10)


@pytest.mark.parametrize('opponent', SOLO_OPPONENTS)
def test_every_tracked_deck_is_playable(opponent):
    deck_file = SOLO_OPPONENT_DECK_FILES[opponent]
    deck_entries = json.loads(deck_file.read_text(encoding='utf-8'))['decks']
    _, card_index = get_card_index()

    assert deck_entries, f'{deck_file.name} lists no decks'
    for deck_entry in deck_entries:
        try:
            validate_deck(definition_indices_for_cards(resolve_deck_cards(deck_entry, card_index)))
        except ValueError as error:
            pytest.fail(f'{deck_file.name} deck {deck_entry.get("guid")}: {error}')


def test_a_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setitem(SOLO_OPPONENT_DECK_FILES, SOLO_OPPONENT_PPO, tmp_path / 'missing.json')

    with pytest.raises(ValueError, match='missing.json'):
        load_solo_opponent_deck(SOLO_OPPONENT_PPO, get_card_index()[1])


def test_a_file_without_decks_raises(monkeypatch, tmp_path):
    deck_file = write_deck_file(tmp_path / 'empty.json', [])
    monkeypatch.setitem(SOLO_OPPONENT_DECK_FILES, SOLO_OPPONENT_PPO, deck_file)

    with pytest.raises(ValueError, match='empty.json'):
        load_solo_opponent_deck(SOLO_OPPONENT_PPO, get_card_index()[1])


@pytest.mark.parametrize('opponent', SOLO_OPPONENTS)
def test_solo_games_deal_the_model_its_own_deck(monkeypatch, tmp_path, opponent):
    decks = install_deck_files(monkeypatch, tmp_path)

    _, events = run_stubbed_solo_game(monkeypatch, opponent)

    assert events == ['deck building', ('match', HUMAN_DECK_SENTINEL, decks[opponent])]


def test_a_broken_deck_file_ends_the_game_before_deck_building(monkeypatch, tmp_path):
    monkeypatch.setitem(SOLO_OPPONENT_DECK_FILES, SOLO_OPPONENT_ALPHA_ZERO, tmp_path / 'missing.json')

    session, events = run_stubbed_solo_game(monkeypatch, SOLO_OPPONENT_ALPHA_ZERO)

    assert events == []
    assert session.transport.player_messages[0] == [{'content': 'An error occurred. Game ended.'}]
