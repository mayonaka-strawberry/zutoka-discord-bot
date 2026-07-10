"""
Support helpers for PostgreSQL integration tests.

Integration tests are skipped unless ZUTOKA_TEST_DATABASE_URL is set (see the
integration_database_url fixture in tests/conftest.py). Each test runs inside
its own event loop via run_with_database, which initializes the pool against
the test database, applies the schema, truncates all data tables, runs the
test coroutine, and closes the pool.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from zutomayo.data import database

DATA_TABLES = (
    'elo_history',
    'game_events',
    'game_decisions',
    'game_players',
    'games',
    'daily_game_counters',
    'decks_tcg',
    'decks',
    'display_names',
    'player_profiles',
)


async def truncate_data_tables() -> None:
    async with database.get_pool().acquire() as connection:
        await connection.execute(
            'TRUNCATE TABLE ' + ', '.join(DATA_TABLES) + ' CASCADE'
        )


def run_with_database(
    database_url: str,
    test_coroutine_function: Callable[[], Awaitable[Any]],
) -> Any:
    """Run one integration-test coroutine against a clean test database."""

    async def runner() -> Any:
        await database.initialize_pool(dsn=database_url)
        try:
            await database.apply_schema()
            await truncate_data_tables()
            return await test_coroutine_function()
        finally:
            await database.close_pool()

    return asyncio.run(runner())
