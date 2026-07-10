"""
Persistence layer for per-user player profiles (Elo rating, win/loss records,
per-deck stats, per-opponent stats, forfeits).

Profiles live in the PostgreSQL player_profiles table, keyed by Discord user
id. Leaderboard-sorted fields (elo, tcg_elo, peaks, game counts) are columns;
the evolving stats / deck_stats / opponent_stats buckets are JSONB and are
run through _migrate_profile on every load so older payload shapes stay
readable.

All storage access goes through the module-level `backend` attribute
(PostgresProfileBackend in production); tests swap in an in-memory fake.
Result-recording functions run in a single transaction with the profile rows
locked (SELECT ... FOR UPDATE) and write elo_history rows for every rating
change when a game_id is supplied.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional


log = logging.getLogger(__name__)


ELO_STARTING_RATING = 1000
ELO_K_FACTOR = 32

BOT_DISCORD_ID = 0  # sentinel; matches GameSessionManager.BOT_DISCORD_ID

PROFILE_JSONB_FIELDS = ('stats', 'deck_stats', 'opponent_stats')
PROFILE_INTEGER_FIELDS = (
    'elo', 'elo_peak', 'elo_games', 'tcg_elo', 'tcg_elo_peak', 'tcg_elo_games',
)

# A mutator receives {user_id: profile_dict} (already migrated), mutates the
# profiles in place, and returns elo_history rows to insert (possibly empty).
ProfileMutator = Callable[[dict[int, dict]], list[dict]]


def _empty_profile(user_id: int) -> dict:
    return {
        'user_id': user_id,
        'last_updated': None,
        'elo': ELO_STARTING_RATING,
        'elo_peak': ELO_STARTING_RATING,
        'elo_games': 0,
        'tcg_elo': ELO_STARTING_RATING,
        'tcg_elo_peak': ELO_STARTING_RATING,
        'tcg_elo_games': 0,
        'stats': {
            'standard':   {'wins': 0, 'losses': 0, 'draws': 0},
            'tcg_match':  {'wins': 0, 'losses': 0, 'draws': 0},
            'tcg_series': {'wins': 0, 'losses': 0},
            'solo_easy':   {'wins': 0, 'losses': 0, 'draws': 0},
            'solo_normal': {'wins': 0, 'losses': 0, 'draws': 0},
            'forfeits_given': 0,
            'forfeits_received': 0,
        },
        'deck_stats': {
            'standard': {},
            'tcg': {},
        },
        'opponent_stats': {},
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_profile(profile: dict, user_id: int) -> dict:
    """Fill in any missing top-level keys from the empty-default so older profiles stay readable."""
    default = _empty_profile(user_id)
    for key, value in default.items():
        profile.setdefault(key, value)
    for stats_key, stats_default in default['stats'].items():
        if isinstance(stats_default, dict):
            current = profile['stats'].setdefault(stats_key, dict(stats_default))
            for inner_key, inner_value in stats_default.items():
                current.setdefault(inner_key, inner_value)
        else:
            profile['stats'].setdefault(stats_key, stats_default)
    profile['deck_stats'].setdefault('standard', {})
    profile['deck_stats'].setdefault('tcg', {})
    profile.setdefault('opponent_stats', {})
    return profile


# ----------------------------------------------------------------------
# PostgreSQL backend
# ----------------------------------------------------------------------


def _profile_from_row(row) -> dict:
    profile = {
        'user_id': row['user_id'],
        'last_updated': row['last_updated'].isoformat() if row['last_updated'] is not None else None,
    }
    for field in PROFILE_INTEGER_FIELDS:
        profile[field] = row[field]
    for field in PROFILE_JSONB_FIELDS:
        profile[field] = row[field]
    return profile


class PostgresProfileBackend:
    async def load_profile(self, user_id: int) -> Optional[dict]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            row = await connection.fetchrow(
                'SELECT * FROM player_profiles WHERE user_id = $1', user_id,
            )
        return _profile_from_row(row) if row is not None else None

    async def save_profile(self, user_id: int, profile: dict) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            await self._upsert_profile(connection, user_id, profile)

    @staticmethod
    async def _upsert_profile(connection, user_id: int, profile: dict) -> None:
        await connection.execute(
            '''
            INSERT INTO player_profiles (
                user_id, last_updated,
                elo, elo_peak, elo_games, tcg_elo, tcg_elo_peak, tcg_elo_games,
                stats, deck_stats, opponent_stats
            ) VALUES ($1, now(), $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (user_id) DO UPDATE SET
                last_updated = now(),
                elo = EXCLUDED.elo,
                elo_peak = EXCLUDED.elo_peak,
                elo_games = EXCLUDED.elo_games,
                tcg_elo = EXCLUDED.tcg_elo,
                tcg_elo_peak = EXCLUDED.tcg_elo_peak,
                tcg_elo_games = EXCLUDED.tcg_elo_games,
                stats = EXCLUDED.stats,
                deck_stats = EXCLUDED.deck_stats,
                opponent_stats = EXCLUDED.opponent_stats
            ''',
            user_id,
            profile['elo'], profile['elo_peak'], profile['elo_games'],
            profile['tcg_elo'], profile['tcg_elo_peak'], profile['tcg_elo_games'],
            profile['stats'], profile['deck_stats'], profile['opponent_stats'],
        )

    async def list_ranked_profiles(
        self, rating_field: str, games_field: str, minimum_games: int,
    ) -> list[dict]:
        from zutomayo.data.database import get_pool

        if rating_field not in PROFILE_INTEGER_FIELDS or games_field not in PROFILE_INTEGER_FIELDS:
            raise ValueError(f'Unknown profile fields: {rating_field}, {games_field}')
        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                f'''
                SELECT * FROM player_profiles
                WHERE {games_field} >= $1 AND user_id != $2
                ORDER BY {rating_field} DESC, user_id
                ''',
                minimum_games, BOT_DISCORD_ID,
            )
        return [_migrate_profile(_profile_from_row(row), row['user_id']) for row in rows]

    async def list_all_profiles(self) -> list[dict]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch('SELECT * FROM player_profiles ORDER BY user_id')
        return [_migrate_profile(_profile_from_row(row), row['user_id']) for row in rows]

    async def mutate_profiles(self, user_ids: list[int], mutator: ProfileMutator) -> None:
        from zutomayo.data.database import get_pool

        ordered_ids = sorted(set(user_ids))  # stable lock order prevents deadlocks
        async with get_pool().acquire() as connection:
            async with connection.transaction():
                profiles: dict[int, dict] = {}
                for user_id in ordered_ids:
                    row = await connection.fetchrow(
                        'SELECT * FROM player_profiles WHERE user_id = $1 FOR UPDATE',
                        user_id,
                    )
                    if row is None:
                        profiles[user_id] = _empty_profile(user_id)
                    else:
                        profiles[user_id] = _migrate_profile(_profile_from_row(row), user_id)

                elo_history_rows = mutator(profiles) or []

                for user_id, profile in profiles.items():
                    await self._upsert_profile(connection, user_id, profile)
                for history_row in elo_history_rows:
                    await connection.execute(
                        '''
                        INSERT INTO elo_history (game_id, user_id, ladder, elo_before, elo_after)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (game_id, user_id, ladder) DO NOTHING
                        ''',
                        history_row['game_id'], history_row['user_id'], history_row['ladder'],
                        history_row['elo_before'], history_row['elo_after'],
                    )


backend = PostgresProfileBackend()


# ----------------------------------------------------------------------
# Module API (same call surface as the old JSON layer, now async)
# ----------------------------------------------------------------------


async def load_profile(user_id: int) -> dict:
    """Load a user's profile. Returns a fresh empty-default profile if none is stored."""
    stored = await backend.load_profile(user_id)
    if stored is None:
        return _empty_profile(user_id)
    return _migrate_profile(stored, user_id)


async def save_profile(user_id: int, profile: dict) -> None:
    profile['last_updated'] = _now_iso()
    await backend.save_profile(user_id, profile)


async def list_ranked_profiles(
    *,
    rating_field: str = 'elo',
    games_field: str = 'elo_games',
    minimum_games: int = 1,
) -> list[dict]:
    """Profiles with at least minimum_games on the given ladder, best rating first."""
    return await backend.list_ranked_profiles(rating_field, games_field, minimum_games)


async def list_all_profiles() -> list[dict]:
    """Every stored profile. Used by the maintenance reset scripts."""
    return await backend.list_all_profiles()


def _expected_score(rating_a: int, rating_b: int) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def _apply_elo_update(
    profile_a: dict,
    profile_b: dict,
    score_a: float,
    *,
    rating_field: str = 'elo',
    peak_field: str = 'elo_peak',
    games_field: str = 'elo_games',
) -> None:
    """
    Update Elo on both profiles. score_a is 1.0 (a wins), 0.5 (draw), or 0.0 (b wins).
    Standard Elo formula: bigger rating gaps already produce asymmetric swings (small for
    favourites, large for upsets) without any custom K-factor tweaks.

    The rating_field / peak_field / games_field parameters let the same updater drive
    both standard PvP Elo and the parallel TCG-series Elo against different profile keys.
    """
    rating_a = profile_a.get(rating_field, ELO_STARTING_RATING)
    rating_b = profile_b.get(rating_field, ELO_STARTING_RATING)
    expected_a = _expected_score(rating_a, rating_b)
    delta_a = round(ELO_K_FACTOR * (score_a - expected_a))

    profile_a[rating_field] = rating_a + delta_a
    profile_b[rating_field] = rating_b - delta_a
    profile_a[peak_field] = max(profile_a.get(peak_field, ELO_STARTING_RATING), profile_a[rating_field])
    profile_b[peak_field] = max(profile_b.get(peak_field, ELO_STARTING_RATING), profile_b[rating_field])
    profile_a[games_field] = profile_a.get(games_field, 0) + 1
    profile_b[games_field] = profile_b.get(games_field, 0) + 1


def _apply_elo_update_with_history(
    profile_a: dict,
    profile_b: dict,
    score_a: float,
    *,
    ladder: str,
    game_id: Optional[str],
    rating_field: str = 'elo',
    peak_field: str = 'elo_peak',
    games_field: str = 'elo_games',
) -> list[dict]:
    """Apply the Elo update and return elo_history rows (empty when no game_id is known)."""
    ratings_before = {
        profile_a['user_id']: profile_a.get(rating_field, ELO_STARTING_RATING),
        profile_b['user_id']: profile_b.get(rating_field, ELO_STARTING_RATING),
    }
    _apply_elo_update(
        profile_a, profile_b, score_a,
        rating_field=rating_field, peak_field=peak_field, games_field=games_field,
    )
    if game_id is None:
        return []
    return [
        {
            'game_id': game_id,
            'user_id': profile['user_id'],
            'ladder': ladder,
            'elo_before': ratings_before[profile['user_id']],
            'elo_after': profile[rating_field],
        }
        for profile in (profile_a, profile_b)
    ]


def _deck_format_bucket(profile: dict, deck_format: str) -> dict:
    return profile['deck_stats'].setdefault(deck_format, {})


def _ensure_deck_entry(profile: dict, deck_format: str, deck_name: Optional[str]) -> dict:
    """Return the mutable deck-stats entry for (deck_format, deck_name). Uses '<random>' if name is None."""
    bucket = _deck_format_bucket(profile, deck_format)
    name_key = deck_name if deck_name is not None else '<random>'
    if name_key not in bucket:
        bucket[name_key] = {
            'pvp':  {'games': 0, 'wins': 0, 'losses': 0, 'draws': 0},
            'solo': {'games': 0, 'wins': 0, 'losses': 0, 'draws': 0},
        }
    return bucket[name_key]


def _ensure_opponent_entry(profile: dict, opponent_id: int) -> dict:
    opponent_key = str(opponent_id)
    if opponent_key not in profile['opponent_stats']:
        profile['opponent_stats'][opponent_key] = {
            'games': 0, 'wins': 0, 'losses': 0, 'draws': 0,
            'last_played': None,
        }
    return profile['opponent_stats'][opponent_key]


def _outcome_keys(player_index: int, winner_index_or_none: Optional[int]) -> tuple[str, float]:
    """Return ('wins'|'losses'|'draws', elo_score) for the given player slot."""
    if winner_index_or_none is None:
        return 'draws', 0.5
    if winner_index_or_none == player_index:
        return 'wins', 1.0
    return 'losses', 0.0


async def record_match_result(
    player_zero_id: int,
    player_one_id: int,
    player_zero_deck_name: Optional[str],
    player_one_deck_name: Optional[str],
    winner_index_or_none: Optional[int],
    *,
    mode: str,
    is_solo: bool,
    solo_difficulty: str = 'normal',
    game_id: Optional[str] = None,
) -> None:
    """
    Record one match's result.

    mode is 'standard' (PvP non-TCG, or solo) or 'tcg_match' (one game inside a TCG series).
    For solo, only the human player's profile is updated and only solo_* counters change.
    For PvP, both profiles are updated: top-level stats, opponent_stats, deck_stats, and Elo
    (standard mode only, with an elo_history row per player when game_id is known).
    """
    if is_solo:
        human_id = player_zero_id if player_zero_id != BOT_DISCORD_ID else player_one_id
        bot_index = 1 if player_zero_id != BOT_DISCORD_ID else 0
        human_index = 1 - bot_index
        human_deck_name = player_zero_deck_name if human_index == 0 else player_one_deck_name

        def solo_mutator(profiles: dict[int, dict]) -> list[dict]:
            profile = profiles[human_id]
            solo_bucket = 'solo_easy' if solo_difficulty == 'easy' else 'solo_normal'
            outcome_key, _ = _outcome_keys(human_index, winner_index_or_none)
            profile['stats'][solo_bucket][outcome_key] += 1

            deck_format = 'standard'  # solo always uses the standard format engine
            deck_entry = _ensure_deck_entry(profile, deck_format, human_deck_name)
            deck_entry['solo']['games'] += 1
            deck_entry['solo'][outcome_key] += 1
            return []

        await backend.mutate_profiles([human_id], solo_mutator)
        return

    def pvp_mutator(profiles: dict[int, dict]) -> list[dict]:
        profile_zero = profiles[player_zero_id]
        profile_one = profiles[player_one_id]

        outcome_zero, score_zero = _outcome_keys(0, winner_index_or_none)
        outcome_one, _ = _outcome_keys(1, winner_index_or_none)

        stats_bucket = 'tcg_match' if mode == 'tcg_match' else 'standard'
        profile_zero['stats'][stats_bucket][outcome_zero] += 1
        profile_one['stats'][stats_bucket][outcome_one] += 1

        deck_format = 'tcg' if mode == 'tcg_match' else 'standard'
        deck_entry_zero = _ensure_deck_entry(profile_zero, deck_format, player_zero_deck_name)
        deck_entry_one = _ensure_deck_entry(profile_one, deck_format, player_one_deck_name)
        deck_entry_zero['pvp']['games'] += 1
        deck_entry_zero['pvp'][outcome_zero] += 1
        deck_entry_one['pvp']['games'] += 1
        deck_entry_one['pvp'][outcome_one] += 1

        opponent_entry_zero = _ensure_opponent_entry(profile_zero, player_one_id)
        opponent_entry_one = _ensure_opponent_entry(profile_one, player_zero_id)
        timestamp = _now_iso()
        for entry, outcome_key in ((opponent_entry_zero, outcome_zero), (opponent_entry_one, outcome_one)):
            entry['games'] += 1
            entry[outcome_key] += 1
            entry['last_played'] = timestamp

        # Elo is intentionally scoped to standard PvP only — TCG matches are tracked but do
        # not move the rating used by the leaderboard.
        if mode == 'standard':
            return _apply_elo_update_with_history(
                profile_zero, profile_one, score_zero,
                ladder='standard', game_id=game_id,
            )
        return []

    await backend.mutate_profiles([player_zero_id, player_one_id], pvp_mutator)


async def record_tcg_series(
    player_zero_id: int,
    player_one_id: int,
    wins_dict: dict,
    *,
    game_id: Optional[str] = None,
) -> None:
    """
    Record series-level TCG result. Per-match stats were handled during the series.

    The TCG Elo ladder updates here (and only here): one Elo move per completed
    best-of-N, regardless of whether it was a sweep or a grind. The standard Elo
    rating is left untouched — TCG has its own parallel rating stored in tcg_elo.
    """
    if BOT_DISCORD_ID in (player_zero_id, player_one_id):
        return  # series-level recording is PvP only

    wins_zero = wins_dict.get(0, 0)
    wins_one = wins_dict.get(1, 0)
    if wins_zero == wins_one:
        return  # tied series shouldn't happen, but skip the write rather than guessing

    def series_mutator(profiles: dict[int, dict]) -> list[dict]:
        profile_zero = profiles[player_zero_id]
        profile_one = profiles[player_one_id]

        if wins_zero > wins_one:
            profile_zero['stats']['tcg_series']['wins'] += 1
            profile_one['stats']['tcg_series']['losses'] += 1
            score_zero = 1.0
        else:
            profile_one['stats']['tcg_series']['wins'] += 1
            profile_zero['stats']['tcg_series']['losses'] += 1
            score_zero = 0.0

        return _apply_elo_update_with_history(
            profile_zero, profile_one, score_zero,
            ladder='tcg', game_id=game_id,
            rating_field='tcg_elo', peak_field='tcg_elo_peak', games_field='tcg_elo_games',
        )

    await backend.mutate_profiles([player_zero_id, player_one_id], series_mutator)


async def record_forfeit(quitter_id: int, opponent_id_or_none: Optional[int]) -> None:
    """
    Forfeit counter only. Does NOT affect Elo or win/loss columns.
    opponent_id_or_none is None for solo (bot opponent) — only the quitter's profile updates.
    """
    if quitter_id == BOT_DISCORD_ID:
        return
    involved_ids = [quitter_id]
    if opponent_id_or_none is not None and opponent_id_or_none != BOT_DISCORD_ID:
        involved_ids.append(opponent_id_or_none)

    def forfeit_mutator(profiles: dict[int, dict]) -> list[dict]:
        profiles[quitter_id]['stats']['forfeits_given'] += 1
        if len(involved_ids) > 1:
            profiles[opponent_id_or_none]['stats']['forfeits_received'] += 1
        return []

    await backend.mutate_profiles(involved_ids, forfeit_mutator)
