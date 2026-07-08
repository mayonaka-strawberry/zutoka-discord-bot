"""Repository-wide pytest fixtures."""

from __future__ import annotations

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
    monkeypatch.setattr(name_storage_module, 'USERNAMES_FILE', tmp_path / 'players' / 'usernames.json')
    monkeypatch.setattr(name_storage_module, '_names_cache', None)
