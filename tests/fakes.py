"""
In-memory storage backends for tests.

The autouse fixture in tests/conftest.py installs these in place of the
PostgreSQL backends so the whole suite runs without a database. They mirror
the backend surfaces in zutomayo/data/player_storage.py and
zutomayo/data/name_storage.py exactly.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from typing import Optional

from zutomayo.data.deck_repository import resolve_card_list, serialize_cards
from zutomayo.data.game_id_allocator import format_game_id
from zutomayo.data.player_storage import (
    BOT_DISCORD_ID,
    ProfileMutator,
    _empty_profile,
    _migrate_profile,
    _now_iso,
)
from zutomayo.models.card import Card


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


class InMemoryDeckRepository:
    """Mirrors PostgresDeckRepository, alphabetical listing included."""

    resolve_card_list = staticmethod(resolve_card_list)

    def __init__(self, card_list_fields: tuple[str, ...]) -> None:
        self.card_list_fields = card_list_fields
        self.decks_by_user: dict[int, dict[str, dict]] = {}

    def _user_decks(self, user_id: int) -> dict[str, dict]:
        return self.decks_by_user.setdefault(user_id, {})

    async def load_user_decks(self, user_id: int) -> list[dict]:
        return [
            copy.deepcopy(self._user_decks(user_id)[name])
            for name in sorted(self._user_decks(user_id))
        ]

    async def save_user_decks(self, user_id: int, decks: list[dict]) -> None:
        self.decks_by_user[user_id] = {
            deck_entry['name']: copy.deepcopy(deck_entry) for deck_entry in decks
        }

    async def get_deck_names(self, user_id: int) -> list[str]:
        return sorted(self._user_decks(user_id))

    async def get_deck_by_name(self, user_id: int, name: str) -> Optional[dict]:
        stored = self._user_decks(user_id).get(name)
        return copy.deepcopy(stored) if stored is not None else None

    async def search_deck_names(self, user_id: int, prefix: str, limit: int = 25) -> list[str]:
        matches = [
            name for name in sorted(self._user_decks(user_id))
            if name.lower().startswith(prefix.lower())
        ]
        return matches[:limit]

    async def add_deck(self, user_id: int, name: str, card_lists: dict[str, list[Card]]) -> None:
        decks = self._user_decks(user_id)
        if name in decks:
            raise ValueError(f'A deck named "{name}" already exists.')
        entry: dict = {'name': name}
        for field in self.card_list_fields:
            entry[field] = serialize_cards(card_lists[field])
        decks[name] = entry

    async def update_deck(self, user_id: int, name: str, card_lists: dict[str, list[Card]]) -> None:
        decks = self._user_decks(user_id)
        if name not in decks:
            raise ValueError(f'Deck "{name}" not found.')
        for field in self.card_list_fields:
            decks[name][field] = serialize_cards(card_lists[field])

    async def delete_deck(self, user_id: int, name: str) -> None:
        decks = self._user_decks(user_id)
        if name not in decks:
            raise ValueError(f'Deck "{name}" not found.')
        del decks[name]

    async def list_all_decks(self) -> list[dict]:
        entries = []
        for user_id in sorted(self.decks_by_user):
            for name in sorted(self.decks_by_user[user_id]):
                entry = copy.deepcopy(self.decks_by_user[user_id][name])
                entry['user_id'] = user_id
                entries.append(entry)
        return entries


class InMemoryGameIdAllocator:
    """Deterministic allocator with per-day counters, mirroring the SQL upsert."""

    def __init__(self, fixed_day: Optional[date] = None) -> None:
        self.fixed_day = fixed_day
        self.counters: dict[date, int] = {}

    async def allocate(self, now: Optional[datetime] = None) -> str:
        if self.fixed_day is not None:
            day = self.fixed_day
        elif now is not None:
            day = now.astimezone(timezone.utc).date()
        else:
            day = datetime.now(timezone.utc).date()
        counter = self.counters.get(day, 0)
        self.counters[day] = counter + 1
        return format_game_id(day, counter)


class InMemoryGameRecordBackend:
    """Mirrors PostgresGameRecordBackend: game rows, decision logs, statuses."""

    def __init__(self) -> None:
        self.games: dict[str, dict] = {}
        self.decisions: dict[str, dict[int, dict]] = {}
        self.events: dict[str, list[dict]] = {}

    async def insert_game(self, manifest: dict) -> None:
        game_id = manifest['game_id']
        if game_id in self.games:
            return
        self.games[game_id] = {
            'game_id': game_id,
            'schema_version': manifest['schema_version'],
            'status': 'active',
            'mode': manifest['mode'],
            'channel_id': manifest['channel_id'],
            'is_solo': manifest['is_solo'],
            'solo_difficulty': manifest['solo_difficulty'],
            'is_tcg': manifest['is_tcg'],
            'best_of': manifest['best_of'],
            'random_seed': manifest['random_seed'],
            'manifest': copy.deepcopy(manifest),
            'winner_index': None,
            'result_summary': None,
            'created_at': datetime.now(timezone.utc),
            'saved_at': None,
            'ended_at': None,
        }
        self.decisions.setdefault(game_id, {})

    async def insert_decision(self, game_id: str, record: dict) -> None:
        log_for_game = self.decisions.setdefault(game_id, {})
        log_for_game.setdefault(record['sequence_number'], copy.deepcopy(record))

    async def update_status(
        self,
        game_id: str,
        status: str,
        *,
        winner_index: Optional[int] = None,
        result_summary: Optional[dict] = None,
        channel_id: Optional[int] = None,
    ) -> None:
        row = self.games.get(game_id)
        if row is None:
            return
        row['status'] = status
        if winner_index is not None:
            row['winner_index'] = winner_index
        if result_summary is not None:
            row['result_summary'] = copy.deepcopy(result_summary)
        if channel_id is not None:
            row['channel_id'] = channel_id
        if status == 'saved':
            row['saved_at'] = datetime.now(timezone.utc)
        if status in ('completed', 'quit', 'abandoned', 'divergence_failed'):
            row['ended_at'] = datetime.now(timezone.utc)

    async def load_manifest(self, game_id: str) -> Optional[dict]:
        row = self.games.get(game_id)
        return copy.deepcopy(row['manifest']) if row is not None else None

    async def load_decision_records(self, game_id: str) -> list[dict]:
        log_for_game = self.decisions.get(game_id, {})
        return [
            copy.deepcopy(log_for_game[sequence_number])
            for sequence_number in sorted(log_for_game)
        ]

    async def insert_events(self, game_id: str, events: list[dict]) -> None:
        stored = self.events.setdefault(game_id, [])
        existing_indices = {event['event_index'] for event in stored}
        for event in events:
            if event['event_index'] not in existing_indices:
                stored.append(copy.deepcopy(event))
        stored.sort(key=lambda event: event['event_index'])

    async def next_event_index(self, game_id: str) -> int:
        stored = self.events.get(game_id, [])
        if not stored:
            return 0
        return stored[-1]['event_index'] + 1

    async def load_events(self, game_id: str) -> list[dict]:
        return [copy.deepcopy(event) for event in self.events.get(game_id, [])]

    async def list_game_ids_with_status(self, status: str) -> list[str]:
        rows = [row for row in self.games.values() if row['status'] == status]
        rows.sort(key=lambda row: row['created_at'])
        return [row['game_id'] for row in rows]

    async def get_game_row(self, game_id: str) -> Optional[dict]:
        row = self.games.get(game_id)
        return copy.deepcopy(row) if row is not None else None

    async def list_saved_games_for_player(
        self, discord_id: int, prefix: str = '', limit: int = 25,
    ) -> list[dict]:
        matching = []
        for row in self.games.values():
            if row['status'] != 'saved' or not row['game_id'].startswith(prefix):
                continue
            player_ids = [pair[0] for pair in row['manifest'].get('player_discord_ids', [])]
            if discord_id not in player_ids:
                continue
            matching.append({
                'game_id': row['game_id'],
                'mode': row['mode'],
                'is_tcg': row['is_tcg'],
                'best_of': row['best_of'],
                'saved_at': row['saved_at'],
            })
        matching.sort(key=lambda row: (row['saved_at'] is None, row['saved_at']), reverse=True)
        return matching[:limit]

    async def list_recent_games_for_player(self, discord_id: int, limit: int = 15) -> list[dict]:
        matching = []
        for row in self.games.values():
            if row['status'] not in ('completed', 'quit', 'abandoned'):
                continue
            seats = {
                pair[0]: pair[1] for pair in row['manifest'].get('player_discord_ids', [])
            }
            if discord_id not in seats:
                continue
            player_index = seats[discord_id]
            opponent_discord_id = next(
                (other_id for other_id, index in seats.items() if index == 1 - player_index),
                None,
            )
            matching.append({
                'game_id': row['game_id'],
                'mode': row['mode'],
                'is_tcg': row['is_tcg'],
                'best_of': row['best_of'],
                'status': row['status'],
                'created_at': row['created_at'],
                'winner_index': row['winner_index'],
                'player_index': player_index,
                'opponent_discord_id': opponent_discord_id,
            })
        matching.sort(key=lambda row: row['created_at'], reverse=True)
        return matching[:limit]

    async def search_finished_games(self, prefix: str = '', limit: int = 25) -> list[dict]:
        matching = [
            {
                'game_id': row['game_id'],
                'mode': row['mode'],
                'is_tcg': row['is_tcg'],
                'best_of': row['best_of'],
                'status': row['status'],
                'created_at': row['created_at'],
                'winner_index': row['winner_index'],
            }
            for row in self.games.values()
            if row['status'] in ('completed', 'quit', 'abandoned')
            and row['game_id'].startswith(prefix)
        ]
        matching.sort(key=lambda row: row['created_at'], reverse=True)
        return matching[:limit]

    def truncate_decision_log(self, game_id: str, keep_first: int) -> None:
        """Test helper: simulate a crash after the first N decisions."""
        log_for_game = self.decisions.get(game_id, {})
        kept_numbers = sorted(log_for_game)[:keep_first]
        self.decisions[game_id] = {
            sequence_number: log_for_game[sequence_number]
            for sequence_number in kept_numbers
        }


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
