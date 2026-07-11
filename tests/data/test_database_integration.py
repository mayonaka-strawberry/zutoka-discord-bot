"""
PostgreSQL foundation integration tests.

Skipped unless ZUTOKA_TEST_DATABASE_URL is set; see docs/postgresql_setup.md.
"""

from __future__ import annotations

import asyncio

from tests.support.database_support import run_with_database
from zutomayo.data import database


def test_schema_applies_and_tables_exist(integration_database_url):
    async def check():
        async with database.get_pool().acquire() as connection:
            rows = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        return {row['tablename'] for row in rows}

    table_names = run_with_database(integration_database_url, check)
    expected = {
        'schema_metadata', 'player_profiles', 'display_names', 'decks',
        'decks_tcg', 'daily_game_counters', 'games', 'game_players',
        'game_decisions', 'game_events', 'elo_history',
    }
    assert expected.issubset(table_names)


def test_jsonb_round_trips_as_python_objects(integration_database_url):
    async def round_trip():
        payload = {'cards': [{'pack': 1, 'id': 3}], 'nested': {'value': 2}}
        async with database.get_pool().acquire() as connection:
            await connection.execute(
                'INSERT INTO decks (user_id, name, cards) VALUES ($1, $2, $3)',
                1, 'round trip', payload,
            )
            return await connection.fetchval(
                'SELECT cards FROM decks WHERE user_id = $1 AND name = $2',
                1, 'round trip',
            )

    stored = run_with_database(integration_database_url, round_trip)
    assert stored == {'cards': [{'pack': 1, 'id': 3}], 'nested': {'value': 2}}


def test_daily_counter_allocation_is_atomic(integration_database_url):
    allocation_sql = (
        'INSERT INTO daily_game_counters (day, next_counter) VALUES ($1, 1) '
        'ON CONFLICT (day) DO UPDATE '
        'SET next_counter = daily_game_counters.next_counter + 1 '
        'RETURNING next_counter - 1'
    )

    async def allocate_many():
        pool = database.get_pool()

        async def allocate_one():
            async with pool.acquire() as connection:
                return await connection.fetchval(allocation_sql, __import__('datetime').date(2026, 7, 10))

        return await asyncio.gather(*[allocate_one() for _ in range(50)])

    counters = run_with_database(integration_database_url, allocate_many)
    assert sorted(counters) == list(range(50))
