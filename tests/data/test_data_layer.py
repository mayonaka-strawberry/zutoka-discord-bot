"""Unit tests for the data layer: deck repository, validators, player storage
(match recording and Elo), name storage, gacha, and the card cache."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import asyncio  # noqa: E402
import random  # noqa: E402

import pytest  # noqa: E402

from zutomayo.data.card_loader import load_cards  # noqa: E402
from zutomayo.data.deck_repository import DeckRepository  # noqa: E402
from zutomayo.data.deck_validator import build_card_index, get_card_index, parse_deck_input  # noqa: E402
from zutomayo.data.deck_validator_tcg import parse_tcg_deck_input  # noqa: E402

from tests.support.game_state_builder import card_by_identity  # noqa: E402

VALID_TWENTY = ('01-013 01-013 01-014 01-014 01-017 01-017 01-020 01-020 01-023 01-023 '
                '01-026 01-026 01-029 01-029 01-032 01-032 01-035 01-035 01-038 01-038')
VALID_EIGHT = '02-005 02-005 02-007 02-007 02-011 02-011 02-014 02-014'


@pytest.fixture()
def repository(tmp_path):
    return DeckRepository(tmp_path / 'decks', ('cards',))


@pytest.fixture()
def tcg_repository(tmp_path):
    return DeckRepository(tmp_path / 'decks_tcg', ('deck', 'side_deck'))


class TestDeckRepository:
    def test_missing_file_loads_empty(self, repository):
        assert repository.load_user_decks(42) == []
        assert repository.get_deck_names(42) == []
        assert repository.get_deck_by_name(42, 'anything') is None

    def test_add_get_update_delete_round_trip(self, repository):
        cards = [card_by_identity('01-013'), card_by_identity('01-014')]
        repository.add_deck(7, 'My Deck', {'cards': cards})
        assert repository.get_deck_names(7) == ['My Deck']

        stored = repository.get_deck_by_name(7, 'My Deck')
        assert stored['cards'] == [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 14}]

        repository.update_deck(7, 'My Deck', {'cards': [card_by_identity('01-017')]})
        assert repository.get_deck_by_name(7, 'My Deck')['cards'] == [{'pack': 1, 'id': 17}]

        repository.delete_deck(7, 'My Deck')
        assert repository.get_deck_names(7) == []

    def test_duplicate_and_missing_names_raise(self, repository):
        repository.add_deck(7, 'My Deck', {'cards': []})
        with pytest.raises(ValueError):
            repository.add_deck(7, 'My Deck', {'cards': []})
        with pytest.raises(ValueError):
            repository.update_deck(7, 'Missing', {'cards': []})
        with pytest.raises(ValueError):
            repository.delete_deck(7, 'Missing')

    def test_writes_are_atomic_files(self, repository, tmp_path):
        repository.add_deck(7, 'My Deck', {'cards': []})
        assert (tmp_path / 'decks' / '7.json').exists()
        assert not (tmp_path / 'decks' / '7.json.tmp').exists()

    def test_tcg_repository_carries_both_card_lists(self, tcg_repository):
        tcg_repository.add_deck(7, 'Series Deck', {
            'deck': [card_by_identity('01-013')],
            'side_deck': [card_by_identity('01-014')],
        })
        stored = tcg_repository.get_deck_by_name(7, 'Series Deck')
        assert stored['deck'] == [{'pack': 1, 'id': 13}]
        assert stored['side_deck'] == [{'pack': 1, 'id': 14}]

    def test_resolve_card_list(self, repository):
        _, card_index = get_card_index()
        cards = DeckRepository.resolve_card_list(
            {'cards': [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 13}]}, 'cards', card_index,
        )
        assert [card.id for card in cards] == [13, 13]
        with pytest.raises(ValueError):
            DeckRepository.resolve_card_list({'cards': [{'pack': 99, 'id': 999}]}, 'cards', card_index)


class TestStorageShims:
    """The deck_storage / deck_storage_tcg modules delegate to the repository;
    exercise every public shim once against patched directories."""

    def test_standard_shims_round_trip(self, tmp_path, monkeypatch):
        import zutomayo.data.deck_storage as deck_storage_module
        from zutomayo.data.deck_repository import DeckRepository

        monkeypatch.setattr(
            deck_storage_module, 'STANDARD_DECK_REPOSITORY',
            DeckRepository(tmp_path / 'decks', ('cards',)),
        )
        cards = [card_by_identity('01-013')]
        deck_storage_module.add_deck(3, 'Shim Deck', cards)
        assert deck_storage_module.get_deck_names(3) == ['Shim Deck']
        deck_storage_module.update_deck(3, 'Shim Deck', cards + cards)
        stored = deck_storage_module.get_deck_by_name(3, 'Shim Deck')
        _, card_index = get_card_index()
        assert len(deck_storage_module.resolve_deck_cards(stored, card_index)) == 2
        decks = deck_storage_module.load_user_decks(3)
        deck_storage_module.save_user_decks(3, decks)
        deck_storage_module.delete_deck(3, 'Shim Deck')
        assert deck_storage_module.load_user_decks(3) == []
        assert deck_storage_module.load_default_decks(), 'default decks ship with the repo'

    def test_tcg_shims_round_trip(self, tmp_path, monkeypatch):
        import zutomayo.data.deck_storage_tcg as deck_storage_tcg_module
        from zutomayo.data.deck_repository import DeckRepository

        monkeypatch.setattr(
            deck_storage_tcg_module, 'TCG_DECK_REPOSITORY',
            DeckRepository(tmp_path / 'decks_tcg', ('deck', 'side_deck')),
        )
        main_cards = [card_by_identity('01-013')]
        side_cards = [card_by_identity('01-014')]
        deck_storage_tcg_module.add_tcg_deck(3, 'Shim TCG', main_cards, side_cards)
        assert deck_storage_tcg_module.get_tcg_deck_names(3) == ['Shim TCG']
        deck_storage_tcg_module.update_tcg_deck(3, 'Shim TCG', main_cards, side_cards + side_cards)
        stored = deck_storage_tcg_module.get_tcg_deck_by_name(3, 'Shim TCG')
        _, card_index = get_card_index()
        resolved_main, resolved_side = deck_storage_tcg_module.resolve_tcg_deck_cards(stored, card_index)
        assert len(resolved_main) == 1 and len(resolved_side) == 2
        decks = deck_storage_tcg_module.load_user_tcg_decks(3)
        deck_storage_tcg_module.save_user_tcg_decks(3, decks)
        deck_storage_tcg_module.delete_tcg_deck(3, 'Shim TCG')
        assert deck_storage_tcg_module.load_user_tcg_decks(3) == []


class TestValidators:
    def test_valid_deck_parses(self):
        _, card_index = get_card_index()
        cards, errors = parse_deck_input(VALID_TWENTY, card_index)
        assert errors == [] and len(cards) == 20

    def test_error_branches(self):
        _, card_index = get_card_index()
        cards, errors = parse_deck_input('', card_index)
        assert cards is None and 'Input is empty' in errors[0]

        cards, errors = parse_deck_input('01-013 nonsense 99-999', card_index)
        assert cards is None
        assert any('Expected exactly 20 cards' in error for error in errors)
        assert any('Invalid format' in error for error in errors)
        assert any('Card not found' in error for error in errors)

        too_many_copies = ' '.join(['01-013'] * 20)
        cards, errors = parse_deck_input(too_many_copies, card_index)
        assert cards is None and any('Too many copies' in error for error in errors)

    def test_tcg_validator_checks_both_lists_and_cross_copies(self):
        _, card_index = get_card_index()
        result, errors = parse_tcg_deck_input(VALID_TWENTY, VALID_EIGHT, card_index)
        assert errors == []
        main_cards, side_cards = result
        assert len(main_cards) == 20 and len(side_cards) == 8

        # A card appearing twice in main and once in side breaks the shared limit.
        overlapping_side = '01-013 02-005 02-007 02-011 02-014 02-018 02-021 02-022'
        result, errors = parse_tcg_deck_input(VALID_TWENTY, overlapping_side, card_index)
        assert result is None
        assert any('across main + side deck' in error for error in errors)

        result, errors = parse_tcg_deck_input('', VALID_EIGHT, card_index)
        assert result is None and any('Main deck is empty' in error for error in errors)


class TestPlayerStorage:
    def test_standard_pvp_match_moves_elo_and_stats(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, 'Alpha', None, 0,
                                      mode='standard', is_solo=False, game_id='20260710-00000')
            return await load_profile(111), await load_profile(222)

        winner, loser = asyncio.run(run())
        assert winner['stats']['standard']['wins'] == 1
        assert loser['stats']['standard']['losses'] == 1
        assert winner['elo'] > 1000 > loser['elo']
        assert winner['elo'] + loser['elo'] == 2000, 'standard Elo is zero-sum'
        assert winner['elo_games'] == 1 and loser['elo_games'] == 1
        assert winner['deck_stats']['standard']['Alpha']['pvp']['wins'] == 1
        assert loser['deck_stats']['standard']['<random>']['pvp']['losses'] == 1
        assert winner['opponent_stats']['222']['wins'] == 1

        history = install_in_memory_backends['profiles'].elo_history
        assert len(history) == 2
        assert {row['user_id'] for row in history} == {111, 222}
        assert all(row['ladder'] == 'standard' for row in history)
        assert all(row['game_id'] == '20260710-00000' for row in history)
        winner_row = next(row for row in history if row['user_id'] == 111)
        assert winner_row['elo_before'] == 1000 and winner_row['elo_after'] == winner['elo']

    def test_elo_history_is_skipped_without_a_game_id(self, install_in_memory_backends):
        from zutomayo.data.player_storage import record_match_result

        asyncio.run(record_match_result(111, 222, None, None, 0, mode='standard', is_solo=False))
        assert install_in_memory_backends['profiles'].elo_history == []

    def test_draws_split_the_elo_score(self):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, None, mode='standard', is_solo=False)
            return await load_profile(111), await load_profile(222)

        profile_zero, profile_one = asyncio.run(run())
        assert profile_zero['elo'] == 1000 and profile_one['elo'] == 1000
        assert profile_zero['stats']['standard']['draws'] == 1

    def test_tcg_match_does_not_move_standard_elo(self):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, 0, mode='tcg_match', is_solo=False)
            return await load_profile(111)

        profile = asyncio.run(run())
        assert profile['elo'] == 1000
        assert profile['stats']['tcg_match']['wins'] == 1

    def test_solo_match_updates_only_the_human(self, install_in_memory_backends):
        from zutomayo.data.player_storage import BOT_DISCORD_ID, load_profile, record_match_result

        async def run():
            await record_match_result(111, BOT_DISCORD_ID, 'Alpha', '<bot>', 0,
                                      mode='standard', is_solo=True, solo_difficulty='easy',
                                      game_id='20260710-00001')
            return await load_profile(111)

        human = asyncio.run(run())
        assert human['stats']['solo_easy']['wins'] == 1
        assert human['elo'] == 1000, 'solo games never move Elo'
        assert human['deck_stats']['standard']['Alpha']['solo']['wins'] == 1
        assert install_in_memory_backends['profiles'].elo_history == [], 'solo games write no elo history'

    def test_tcg_series_moves_the_parallel_ladder(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_tcg_series

        async def run():
            await record_tcg_series(111, 222, {0: 2, 1: 1}, game_id='20260710-00002')
            return await load_profile(111), await load_profile(222)

        winner, loser = asyncio.run(run())
        assert winner['stats']['tcg_series']['wins'] == 1
        assert winner['tcg_elo'] > 1000 > loser['tcg_elo']
        assert winner['elo'] == 1000, 'the standard ladder is untouched'

        history = install_in_memory_backends['profiles'].elo_history
        assert len(history) == 2 and all(row['ladder'] == 'tcg' for row in history)

    def test_tcg_series_skips_bot_and_ties(self):
        from zutomayo.data.player_storage import BOT_DISCORD_ID, load_profile, record_tcg_series

        async def run():
            await record_tcg_series(111, BOT_DISCORD_ID, {0: 2, 1: 0})
            await record_tcg_series(111, 222, {0: 1, 1: 1})
            return await load_profile(111)

        profile = asyncio.run(run())
        assert profile['stats']['tcg_series']['wins'] == 0

    def test_record_forfeit(self):
        from zutomayo.data.player_storage import load_profile, record_forfeit

        async def run():
            await record_forfeit(111, 222)
            await record_forfeit(111, None)
            return await load_profile(111), await load_profile(222)

        quitter, opponent = asyncio.run(run())
        assert quitter['stats']['forfeits_given'] == 2
        assert opponent['stats']['forfeits_received'] == 1

    def test_list_ranked_profiles_orders_and_filters(self):
        from zutomayo.data.player_storage import list_ranked_profiles, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, 0, mode='standard', is_solo=False)
            return await list_ranked_profiles(minimum_games=1)

        ranked = asyncio.run(run())
        assert [profile['user_id'] for profile in ranked] == [111, 222]
        assert ranked[0]['elo'] > ranked[1]['elo']


class TestNameStorage:
    def test_remember_resolve_and_custom_names(self, install_in_memory_backends):
        from zutomayo.data import name_storage

        async def run():
            name_storage.remember_user(5, 'Alpha')
            assert name_storage.get_stored_display_name(5) == 'Alpha'
            assert name_storage.resolve_display_name(None, 5) == 'Alpha'

            # Custom names survive automatic capture.
            await name_storage.set_custom_name(5, 'CustomName')
            name_storage.remember_user(5, 'SomethingElse')
            assert name_storage.resolve_display_name(None, 5) == 'CustomName'

            await name_storage.clear_custom_name(5)
            name_storage.remember_user(5, 'Fresh')
            assert name_storage.resolve_display_name(None, 5) == 'Fresh'
            # Give the fire-and-forget persist tasks one loop tick to flush.
            await asyncio.sleep(0)

        asyncio.run(run())
        # remember_user persists through the backend when a loop is running.
        assert install_in_memory_backends['names'].names['5'] == {'name': 'Fresh', 'custom': False}

    def test_cache_reload_reflects_backend_contents(self, install_in_memory_backends):
        from zutomayo.data import name_storage

        async def run():
            await install_in_memory_backends['names'].upsert(9, 'Stored', False)
            await name_storage.load_display_name_cache()
            return name_storage.get_stored_display_name(9)

        assert asyncio.run(run()) == 'Stored'

    def test_unknown_user_falls_back_to_id_suffix(self):
        from zutomayo.data import name_storage

        assert name_storage.resolve_display_name(None, 123456789) == 'User#6789'


class TestGacha:
    def test_draws_are_seed_deterministic_and_from_the_right_pack(self):
        from zutomayo.data.gacha import draw_gacha

        all_cards = load_cards()
        random.seed(77)
        first = draw_gacha(2, all_cards)
        random.seed(77)
        second = draw_gacha(2, all_cards)
        assert [card.id for card in first] == [card.id for card in second]
        assert len(first) == 5
        assert all(card.pack == 2 for card in first)

    def test_gachabox_draws_fifty_cards(self):
        from zutomayo.data.gacha import draw_gachabox

        all_cards = load_cards()
        random.seed(77)
        box = draw_gachabox(1, all_cards)
        assert len(box) == 50
        assert all(card.pack == 1 for card in box)


class TestCardLoader:
    def test_cache_serves_fresh_lists_of_shared_cards(self):
        first = load_cards()
        second = load_cards()
        assert first == second and first is not second
        assert first[0] is second[0]

    def test_get_card_index_is_memoized(self):
        cards_a, index_a = get_card_index()
        cards_b, index_b = get_card_index()
        assert index_a is index_b
        assert cards_a == cards_b and cards_a is not cards_b
        assert build_card_index(cards_a) == index_a