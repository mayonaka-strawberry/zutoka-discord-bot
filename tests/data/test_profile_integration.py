"""
PostgreSQL integration tests for player profiles and display names.

Skipped unless ZUTOKA_TEST_DATABASE_URL is set; see docs/postgresql_setup.md.
"""

from __future__ import annotations

from tests.support.database_support import run_with_database
from zutomayo.data import database


async def _insert_game_row(game_id: str) -> None:
    """elo_history rows reference games; give them a target."""
    async with database.get_pool().acquire() as connection:
        await connection.execute(
            '''
            INSERT INTO games (game_id, schema_version, status, mode, channel_id,
                               is_solo, is_tcg, random_seed, manifest)
            VALUES ($1, 1, 'completed', 'standard', 0, FALSE, FALSE, 1, '{}')
            ''',
            game_id,
        )


def _use_postgres_backends(monkeypatch):
    import zutomayo.data.name_storage as name_storage_module
    import zutomayo.data.player_storage as player_storage_module

    monkeypatch.setattr(
        player_storage_module, 'backend', player_storage_module.PostgresProfileBackend(),
    )
    monkeypatch.setattr(
        name_storage_module, 'backend', name_storage_module.PostgresNameBackend(),
    )
    monkeypatch.setattr(name_storage_module, '_names_cache', None)


def test_profile_round_trip_and_migration(integration_database_url, monkeypatch):
    _use_postgres_backends(monkeypatch)
    from zutomayo.data.player_storage import load_profile, save_profile

    async def round_trip():
        profile = await load_profile(42)
        profile['elo'] = 1234
        profile['stats']['standard']['wins'] = 7
        await save_profile(42, profile)
        return await load_profile(42)

    stored = run_with_database(integration_database_url, round_trip)
    assert stored['elo'] == 1234
    assert stored['stats']['standard']['wins'] == 7
    assert stored['last_updated'] is not None
    assert stored['stats']['tcg_series'] == {'wins': 0, 'losses': 0}, 'migration fills defaults'


def test_match_recording_transaction_writes_elo_history(integration_database_url, monkeypatch):
    _use_postgres_backends(monkeypatch)
    from zutomayo.data.player_storage import load_profile, record_match_result

    async def record():
        await _insert_game_row('20260710-00000')
        await record_match_result(
            111, 222, 'Alpha', None, 0,
            mode='standard', is_solo=False, game_id='20260710-00000',
        )
        async with database.get_pool().acquire() as connection:
            history_rows = await connection.fetch(
                'SELECT * FROM elo_history ORDER BY user_id',
            )
        return await load_profile(111), await load_profile(222), history_rows

    winner, loser, history_rows = run_with_database(integration_database_url, record)
    assert winner['elo'] > 1000 > loser['elo']
    assert winner['elo'] + loser['elo'] == 2000
    assert len(history_rows) == 2
    assert history_rows[0]['user_id'] == 111
    assert history_rows[0]['elo_before'] == 1000
    assert history_rows[0]['elo_after'] == winner['elo']
    assert history_rows[0]['ladder'] == 'standard'


def test_ranked_listing_orders_by_rating(integration_database_url, monkeypatch):
    _use_postgres_backends(monkeypatch)
    from zutomayo.data.player_storage import list_ranked_profiles, record_match_result

    async def rank():
        await record_match_result(111, 222, None, None, 0, mode='standard', is_solo=False)
        return await list_ranked_profiles(minimum_games=1)

    ranked = run_with_database(integration_database_url, rank)
    assert [profile['user_id'] for profile in ranked] == [111, 222]


def test_display_names_round_trip_and_search(integration_database_url, monkeypatch):
    _use_postgres_backends(monkeypatch)
    from zutomayo.data import name_storage
    from zutomayo.data.player_storage import save_profile, _empty_profile

    async def exercise():
        await name_storage.load_display_name_cache()
        await name_storage.set_custom_name(5, 'Sakura')
        name_storage.remember_user(6, 'Sakuma')
        await name_storage.backend.upsert(7, 'Alto', False)
        # Only users with profiles appear in the player search.
        await save_profile(5, _empty_profile(5))
        await save_profile(7, _empty_profile(7))

        await name_storage.load_display_name_cache()
        resolved = name_storage.resolve_display_name(None, 5)
        matches = await name_storage.search_known_players('sak')
        return resolved, matches

    resolved, matches = run_with_database(integration_database_url, exercise)
    assert resolved == 'Sakura'
    assert matches == [(5, 'Sakura')]
