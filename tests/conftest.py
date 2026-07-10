"""Repository-wide pytest fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def install_in_memory_backends(monkeypatch):
    """
    Swap every storage backend for an in-memory fake so tests never touch
    PostgreSQL (or live data). The fakes are returned for assertions.
    """
    import zutomayo.data.name_storage as name_storage_module
    import zutomayo.data.player_storage as player_storage_module

    from tests.fakes import InMemoryNameBackend, InMemoryProfileBackend

    profile_backend = InMemoryProfileBackend()
    name_backend = InMemoryNameBackend()
    name_backend.profile_backend = profile_backend

    monkeypatch.setattr(player_storage_module, 'backend', profile_backend)
    monkeypatch.setattr(name_storage_module, 'backend', name_backend)
    monkeypatch.setattr(name_storage_module, '_names_cache', None)

    return {'profiles': profile_backend, 'names': name_backend}


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
