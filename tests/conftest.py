"""Repository-wide pytest fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_player_data(tmp_path, monkeypatch):
    """
    Keep every test away from the live player profiles and username cache.
    (The standalone regression harnesses do their own equivalent patching.)
    """
    import zutomayo.data.name_storage as name_storage_module
    import zutomayo.data.player_storage as player_storage_module

    monkeypatch.setattr(player_storage_module, 'PLAYERS_DIRECTORY', tmp_path / 'players')
    monkeypatch.setattr(name_storage_module, 'PLAYERS_DIRECTORY', tmp_path / 'players')
    monkeypatch.setattr(name_storage_module, 'USERNAMES_FILE', tmp_path / 'players' / 'usernames.json')
    monkeypatch.setattr(name_storage_module, '_names_cache', None)


@pytest.fixture
def integration_database_url() -> str:
    """
    Connection URL for the PostgreSQL integration-test database. Tests using
    this fixture are skipped unless ZUTOKA_TEST_DATABASE_URL is set (see
    docs/postgresql_setup.md). Use tests.support.database_support
    .run_with_database to run a test coroutine against a clean database.
    """
    database_url = os.environ.get('ZUTOKA_TEST_DATABASE_URL')
    if not database_url:
        pytest.skip('ZUTOKA_TEST_DATABASE_URL is not set; PostgreSQL integration tests are disabled')
    return database_url
