"""
In-memory storage backends for tests.

The autouse fixture in tests/conftest.py installs these in place of the
PostgreSQL backends so the whole suite runs without a database. They mirror
the backend surfaces in zutomayo/data/player_storage.py and
zutomayo/data/name_storage.py exactly.
"""

from __future__ import annotations

import copy
from typing import Optional

from zutomayo.data.player_storage import (
    BOT_DISCORD_ID,
    ProfileMutator,
    _empty_profile,
    _migrate_profile,
    _now_iso,
)


class InMemoryProfileBackend:
    def __init__(self) -> None:
        self.profiles: dict[int, dict] = {}
        self.elo_history: list[dict] = []

    async def load_profile(self, user_id: int) -> Optional[dict]:
        stored = self.profiles.get(user_id)
        return copy.deepcopy(stored) if stored is not None else None

    async def save_profile(self, user_id: int, profile: dict) -> None:
        profile['last_updated'] = _now_iso()
        self.profiles[user_id] = copy.deepcopy(profile)

    async def list_ranked_profiles(
        self, rating_field: str, games_field: str, minimum_games: int,
    ) -> list[dict]:
        rows = [
            copy.deepcopy(profile) for user_id, profile in self.profiles.items()
            if profile.get(games_field, 0) >= minimum_games and user_id != BOT_DISCORD_ID
        ]
        rows.sort(key=lambda profile: (-profile.get(rating_field, 0), profile['user_id']))
        return [_migrate_profile(row, row['user_id']) for row in rows]

    async def list_all_profiles(self) -> list[dict]:
        return [
            _migrate_profile(copy.deepcopy(profile), user_id)
            for user_id, profile in sorted(self.profiles.items())
        ]

    async def mutate_profiles(self, user_ids: list[int], mutator: ProfileMutator) -> None:
        working: dict[int, dict] = {}
        for user_id in sorted(set(user_ids)):
            stored = self.profiles.get(user_id)
            if stored is None:
                working[user_id] = _empty_profile(user_id)
            else:
                working[user_id] = _migrate_profile(copy.deepcopy(stored), user_id)

        elo_history_rows = mutator(working) or []

        for user_id, profile in working.items():
            profile['last_updated'] = _now_iso()
            self.profiles[user_id] = profile
        self.elo_history.extend(copy.deepcopy(row) for row in elo_history_rows)


class InMemoryNameBackend:
    def __init__(self) -> None:
        self.names: dict[str, dict] = {}
        self.profile_backend: Optional[InMemoryProfileBackend] = None

    async def load_all(self) -> dict[str, dict]:
        return copy.deepcopy(self.names)

    async def upsert(self, user_id: int, name: str, custom: bool) -> None:
        self.names[str(user_id)] = {'name': name, 'custom': custom}

    async def delete(self, user_id: int) -> None:
        self.names.pop(str(user_id), None)

    async def search_known_players(self, prefix: str, limit: int = 25) -> list[tuple[int, str]]:
        profile_ids = (
            set(self.profile_backend.profiles) if self.profile_backend is not None else set()
        )
        matches = [
            (int(user_id_string), entry['name'])
            for user_id_string, entry in self.names.items()
            if entry['name'].lower().startswith(prefix.lower())
            and int(user_id_string) in profile_ids
        ]
        matches.sort(key=lambda pair: (pair[1], pair[0]))
        return matches[:limit]
