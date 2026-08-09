"""Unit tests for the data layer: deck repository, validators, player storage
(match recording and Elo), name storage, gacha, and the card cache."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import asyncio  # noqa: E402
import random  # noqa: E402

import pytest  # noqa: E402

from zutomayo.data.card_loader import load_cards  # noqa: E402
from zutomayo.data.deck_validator import build_card_index, get_card_index, parse_deck_input  # noqa: E402
from zutomayo.data.deck_validator_tcg import parse_tcg_deck_input  # noqa: E402

from tests.support.cards import card_by_identity  # noqa: E402

VALID_TWENTY = ('01-013 01-013 01-014 01-014 01-017 01-017 01-020 01-020 01-023 01-023 '
                '01-026 01-026 01-029 01-029 01-032 01-032 01-035 01-035 01-038 01-038')
VALID_EIGHT = '02-005 02-005 02-007 02-007 02-011 02-011 02-014 02-014'


@pytest.fixture()
def repository():
    from tests.fakes import InMemoryDeckRepository
    return InMemoryDeckRepository(('cards',))


@pytest.fixture()
def tcg_repository():
    from tests.fakes import InMemoryDeckRepository
    return InMemoryDeckRepository(('deck', 'side_deck'))


class TestDeckRepository:
    def test_unknown_user_loads_empty(self, repository):
        async def run():
            return (
                await repository.load_user_decks(42),
                await repository.get_deck_names(42),
                await repository.get_deck_by_name(42, 'anything'),
            )

        decks, names, missing = asyncio.run(run())
        assert decks == [] and names == [] and missing is None

    def test_add_get_update_delete_round_trip(self, repository):
        async def run():
            cards = [card_by_identity('01-013'), card_by_identity('01-014')]
            await repository.add_deck(7, 'My Deck', {'cards': cards})
            assert await repository.get_deck_names(7) == ['My Deck']

            stored = await repository.get_deck_by_name(7, 'My Deck')
            assert stored['cards'] == [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 14}]

            await repository.update_deck(7, 'My Deck', {'cards': [card_by_identity('01-017')]})
            updated = await repository.get_deck_by_name(7, 'My Deck')
            assert updated['cards'] == [{'pack': 1, 'id': 17}]

            await repository.delete_deck(7, 'My Deck')
            assert await repository.get_deck_names(7) == []

        asyncio.run(run())

    def test_duplicate_and_missing_names_raise(self, repository):
        async def run():
            await repository.add_deck(7, 'My Deck', {'cards': []})
            with pytest.raises(ValueError):
                await repository.add_deck(7, 'My Deck', {'cards': []})
            with pytest.raises(ValueError):
                await repository.update_deck(7, 'Missing', {'cards': []})
            with pytest.raises(ValueError):
                await repository.delete_deck(7, 'Missing')

        asyncio.run(run())

    def test_deck_names_are_listed_alphabetically_and_searchable(self, repository):
        async def run():
            for name in ('Zeta', 'alpha', 'Alto'):
                await repository.add_deck(7, name, {'cards': []})
            names = await repository.get_deck_names(7)
            matches = await repository.search_deck_names(7, 'al')
            return names, matches

        names, matches = asyncio.run(run())
        assert names == sorted(names)
        assert set(matches) == {'alpha', 'Alto'}

    def test_tcg_repository_carries_both_card_lists(self, tcg_repository):
        async def run():
            await tcg_repository.add_deck(7, 'Series Deck', {
                'deck': [card_by_identity('01-013')],
                'side_deck': [card_by_identity('01-014')],
            })
            return await tcg_repository.get_deck_by_name(7, 'Series Deck')

        stored = asyncio.run(run())
        assert stored['deck'] == [{'pack': 1, 'id': 13}]
        assert stored['side_deck'] == [{'pack': 1, 'id': 14}]

    def test_resolve_card_list(self):
        from zutomayo.data.deck_repository import resolve_card_list

        _, card_index = get_card_index()
        cards = resolve_card_list(
            {'cards': [{'pack': 1, 'id': 13}, {'pack': 1, 'id': 13}]}, 'cards', card_index,
        )
        assert [card.id for card in cards] == [13, 13]
        with pytest.raises(ValueError):
            resolve_card_list({'cards': [{'pack': 99, 'id': 999}]}, 'cards', card_index)


class TestStorageShims:
    """The deck_storage / deck_storage_tcg modules delegate to the repository
    singletons (swapped for in-memory fakes by the autouse fixture); exercise
    every public shim once."""

    def test_standard_shims_round_trip(self):
        import zutomayo.data.deck_storage as deck_storage_module

        async def run():
            cards = [card_by_identity('01-013')]
            await deck_storage_module.add_deck(3, 'Shim Deck', cards)
            assert await deck_storage_module.get_deck_names(3) == ['Shim Deck']
            await deck_storage_module.update_deck(3, 'Shim Deck', cards + cards)
            stored = await deck_storage_module.get_deck_by_name(3, 'Shim Deck')
            _, card_index = get_card_index()
            assert len(deck_storage_module.resolve_deck_cards(stored, card_index)) == 2
            assert await deck_storage_module.search_deck_names(3, 'shim') == ['Shim Deck']
            decks = await deck_storage_module.load_user_decks(3)
            await deck_storage_module.save_user_decks(3, decks)
            await deck_storage_module.delete_deck(3, 'Shim Deck')
            assert await deck_storage_module.load_user_decks(3) == []
            assert deck_storage_module.load_default_decks(), 'default decks ship with the repo'

        asyncio.run(run())

    def test_tcg_shims_round_trip(self):
        import zutomayo.data.deck_storage_tcg as deck_storage_tcg_module

        async def run():
            main_cards = [card_by_identity('01-013')]
            side_cards = [card_by_identity('01-014')]
            await deck_storage_tcg_module.add_tcg_deck(3, 'Shim TCG', main_cards, side_cards)
            assert await deck_storage_tcg_module.get_tcg_deck_names(3) == ['Shim TCG']
            await deck_storage_tcg_module.update_tcg_deck(3, 'Shim TCG', main_cards, side_cards + side_cards)
            stored = await deck_storage_tcg_module.get_tcg_deck_by_name(3, 'Shim TCG')
            _, card_index = get_card_index()
            resolved_main, resolved_side = deck_storage_tcg_module.resolve_tcg_deck_cards(stored, card_index)
            assert len(resolved_main) == 1 and len(resolved_side) == 2
            assert await deck_storage_tcg_module.search_tcg_deck_names(3, 'shim') == ['Shim TCG']
            decks = await deck_storage_tcg_module.load_user_tcg_decks(3)
            await deck_storage_tcg_module.save_user_tcg_decks(3, decks)
            await deck_storage_tcg_module.delete_tcg_deck(3, 'Shim TCG')
            assert await deck_storage_tcg_module.load_user_tcg_decks(3) == []

        asyncio.run(run())


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

    # -- turn-1 self-defeat: the loser pays, the winner gains nothing ----------

    def test_thrown_game_charges_the_loser_and_pays_the_winner_nothing(
        self, install_in_memory_backends,
    ):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, 'Alpha', 'Beta', 0,
                                      mode='standard', is_solo=False,
                                      game_id='20260724-00000',
                                      suppress_winner_elo_gain=True)
            return await load_profile(111), await load_profile(222)

        winner, loser = asyncio.run(run())
        assert loser['elo'] < 1000, 'the thrower still pays in full'
        assert winner['elo'] == 1000, 'a wintrade must not pay out'
        assert winner['elo_peak'] == 1000 and winner['elo_games'] == 0
        assert loser['elo_games'] == 1

    def test_thrown_game_still_records_win_loss_history(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, 'Alpha', 'Beta', 0,
                                      mode='standard', is_solo=False,
                                      game_id='20260724-00001',
                                      suppress_winner_elo_gain=True)
            return await load_profile(111), await load_profile(222)

        winner, loser = asyncio.run(run())
        assert winner['stats']['standard']['wins'] == 1
        assert loser['stats']['standard']['losses'] == 1
        assert winner['deck_stats']['standard']['Alpha']['pvp']['wins'] == 1
        assert loser['deck_stats']['standard']['Beta']['pvp']['losses'] == 1
        assert winner['opponent_stats']['222']['wins'] == 1
        assert loser['opponent_stats']['111']['losses'] == 1

    def test_thrown_game_charges_a_flat_penalty_plus_five_times_the_normal_loss(
        self, install_in_memory_backends,
    ):
        from zutomayo.data.player_storage import (
            SELF_DEFEAT_ELO_LOSS_FLAT_PENALTY, SELF_DEFEAT_ELO_LOSS_MULTIPLIER,
            load_profile, record_match_result,
        )

        async def run(suppress: bool):
            await record_match_result(111, 222, None, None, 0,
                                      mode='standard', is_solo=False,
                                      suppress_winner_elo_gain=suppress)
            return (await load_profile(222))['elo']

        suppressed_elo = asyncio.run(run(True))
        install_in_memory_backends['profiles'].profiles.clear()
        normal_elo = asyncio.run(run(False))

        # Both players start at 1000, where the base loss is exactly 16 and the two
        # roundings coincide. Away from parity round(5x) != 5 * round(x), so this exact
        # identity is a property of equal starting ratings, not of the penalty.
        expected_drop = (
            SELF_DEFEAT_ELO_LOSS_FLAT_PENALTY
            + SELF_DEFEAT_ELO_LOSS_MULTIPLIER * (1000 - normal_elo)
        )
        assert 1000 - suppressed_elo == expected_drop, 'throwing costs far more than losing'

    def test_thrown_game_penalty_never_decays_to_nothing(self, install_in_memory_backends):
        """The flat term is what stops a feeder account from eventually throwing for free.

        Once the thrower is far enough below the winner the scaled part rounds away, so
        without the flat penalty (or with the multiplier applied outside the round) the
        rating would stop moving and the rule would quietly switch itself off.
        """
        from zutomayo.data.player_storage import (
            SELF_DEFEAT_ELO_LOSS_FLAT_PENALTY, load_profile, record_match_result,
            save_profile,
        )

        async def seed_elo(user_id: int, elo: int) -> None:
            profile = await load_profile(user_id)
            profile['elo'] = elo
            await save_profile(user_id, profile)

        async def run():
            await seed_elo(111, 1400)
            await seed_elo(222, 100)
            await record_match_result(111, 222, None, None, 0,
                                      mode='standard', is_solo=False,
                                      suppress_winner_elo_gain=True)
            return await load_profile(222)

        loser = asyncio.run(run())
        assert 100 - loser['elo'] >= SELF_DEFEAT_ELO_LOSS_FLAT_PENALTY

    def test_thrown_game_floors_the_rating_at_zero(self, install_in_memory_backends):
        """Both players are seeded low so the penalty exceeds the thrower's whole rating.

        The penalty scales with the rating gap, so a thrower near zero facing a normal
        opponent is charged only a few points and never reaches the clamp - it takes two
        players who have both ground down to the floor to get there.
        """
        from zutomayo.data.player_storage import (
            ELO_MINIMUM_RATING, load_profile, record_match_result, save_profile,
        )

        async def run():
            await seed_elo(111, 50)
            await seed_elo(222, 50)
            await record_match_result(111, 222, None, None, 0,
                                      mode='standard', is_solo=False,
                                      game_id='20260809-00000',
                                      suppress_winner_elo_gain=True)
            return await load_profile(222)

        async def seed_elo(user_id: int, elo: int) -> None:
            profile = await load_profile(user_id)
            profile['elo'] = elo
            await save_profile(user_id, profile)

        loser = asyncio.run(run())
        assert loser['elo'] == ELO_MINIMUM_RATING, 'a rating must never go negative'

    def test_floored_throw_records_the_real_drop_not_the_notional_one(
        self, install_in_memory_backends,
    ):
        from zutomayo.data.player_storage import load_profile, record_match_result, save_profile

        async def seed_elo(user_id: int, elo: int) -> None:
            profile = await load_profile(user_id)
            profile['elo'] = elo
            await save_profile(user_id, profile)

        async def run():
            await seed_elo(111, 50)
            await seed_elo(222, 50)
            await record_match_result(111, 222, None, None, 0,
                                      mode='standard', is_solo=False,
                                      game_id='20260809-00001',
                                      suppress_winner_elo_gain=True)
            return await load_profile(222)

        loser = asyncio.run(run())
        history = install_in_memory_backends['profiles'].elo_history
        assert len(history) == 1
        assert history[0]['elo_before'] == 50
        assert history[0]['elo_after'] == 0 == loser['elo'], 'the audit trail follows the clamp'

    def test_thrown_game_writes_one_elo_history_row(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, 0,
                                      mode='standard', is_solo=False,
                                      game_id='20260724-00002',
                                      suppress_winner_elo_gain=True)
            return await load_profile(222)

        loser = asyncio.run(run())
        history = install_in_memory_backends['profiles'].elo_history
        assert len(history) == 1, 'nothing happened to the winner to record'
        assert history[0]['user_id'] == 222
        assert history[0]['ladder'] == 'standard'
        assert history[0]['elo_before'] == 1000
        assert history[0]['elo_after'] == loser['elo']

    def test_thrown_game_without_a_game_id_writes_no_history(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, 0,
                                      mode='standard', is_solo=False,
                                      suppress_winner_elo_gain=True)
            return await load_profile(111), await load_profile(222)

        winner, loser = asyncio.run(run())
        assert install_in_memory_backends['profiles'].elo_history == []
        assert winner['elo'] == 1000 and loser['elo'] < 1000

    def test_thrown_tcg_match_moves_no_ladder(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, 0,
                                      mode='tcg_match', is_solo=False,
                                      game_id='20260724-00003',
                                      suppress_winner_elo_gain=True)
            return await load_profile(111), await load_profile(222)

        winner, loser = asyncio.run(run())
        assert winner['elo'] == 1000 and loser['elo'] == 1000
        assert winner['tcg_elo'] == 1000 and loser['tcg_elo'] == 1000
        assert winner['stats']['tcg_match']['wins'] == 1

    def test_thrown_flag_is_ignored_on_a_draw(self, install_in_memory_backends):
        from zutomayo.data.player_storage import load_profile, record_match_result

        async def run():
            await record_match_result(111, 222, None, None, None,
                                      mode='standard', is_solo=False,
                                      suppress_winner_elo_gain=True)
            return await load_profile(111), await load_profile(222)

        profile_zero, profile_one = asyncio.run(run())
        assert profile_zero['elo'] == 1000 and profile_one['elo'] == 1000
        assert profile_zero['stats']['standard']['draws'] == 1

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


class TestCardArtPaths:
    """The renderers assume every card resolves to a real jpg in its pack's directory.

    create_deck_grid_image skips art it cannot open, which shifts the grid rather than
    raising, and the board renderer substitutes the card back -- so a broken path is
    invisible at runtime. These assertions are what make it visible.
    """

    def test_every_card_image_exists_on_disk(self):
        missing = [
            f'{card.pack}-{card.id:03d} {card.name} -> {card.image!r}'
            for card in load_cards()
            if not card.image or not (REPOSITORY_ROOT / card.image).is_file()
        ]
        assert not missing, 'cards with missing image files: ' + '; '.join(missing)

    def test_every_card_image_is_a_jpg(self):
        wrong_format = [
            card.image for card in load_cards()
            if not card.image.lower().endswith('.jpg')
        ]
        assert not wrong_format, f'non-jpg card art: {wrong_format[:5]}'

    def test_image_directory_matches_the_pack_field(self):
        """card_art derives the corner radius from the directory, so the two must agree."""
        mismatched = [
            (card.image, card.pack) for card in load_cards()
            if PurePosixPath(card.image).parent.name != str(card.pack)
        ]
        assert not mismatched, f'image directory disagrees with pack: {mismatched[:5]}'

    def test_every_pack_has_a_corner_radius_constant(self):
        """A new pack must not fall through to the default radius unnoticed."""
        from zutomayo.ui.card_art import CORNER_RADIUS_BY_PACK_DIRECTORY

        unknown = sorted({
            PurePosixPath(card.image).parent.name for card in load_cards()
            if PurePosixPath(card.image).parent.name not in CORNER_RADIUS_BY_PACK_DIRECTORY
        })
        assert not unknown, f'packs without a corner radius constant: {unknown}'