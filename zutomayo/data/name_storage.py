"""
Persistence for Discord user display names, keyed by user ID.

Interactions always include the acting user's name regardless of gateway
intents, so the bot captures names there (see GameCog.capture_interaction_user_name)
instead of relying on the member cache, which is empty now that the privileged
members intent is no longer requested.

Names live in the PostgreSQL display_names table. Because resolve_display_name
is called from synchronous rendering code (transports, embed builders), the
module keeps a write-through in-memory cache: load_display_name_cache() fills
it at startup, reads are cache-only and synchronous, and writes update the
cache immediately and persist through the module-level `backend` (upserts are
fire-and-forget when no await point is available). Entries with custom=True
were set via /zutomayo editname and are never overwritten by automatic capture.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

import discord


log = logging.getLogger(__name__)


MAXIMUM_CUSTOM_NAME_LENGTH = 32

_names_cache: Optional[dict[str, dict]] = None


class PostgresNameBackend:
    async def load_all(self) -> dict[str, dict]:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch('SELECT user_id, name, custom FROM display_names')
        return {str(row['user_id']): {'name': row['name'], 'custom': row['custom']} for row in rows}

    async def upsert(self, user_id: int, name: str, custom: bool) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            await connection.execute(
                '''
                INSERT INTO display_names (user_id, name, custom, updated_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (user_id) DO UPDATE
                SET name = EXCLUDED.name, custom = EXCLUDED.custom, updated_at = now()
                ''',
                user_id, name, custom,
            )

    async def delete(self, user_id: int) -> None:
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            await connection.execute('DELETE FROM display_names WHERE user_id = $1', user_id)

    async def search_known_players(self, prefix: str, limit: int = 25) -> list[tuple[int, str]]:
        """
        Known players whose display name starts with the prefix (case
        insensitive), restricted to users that actually have a profile so the
        profilestats / history autocomplete only offers real players.
        """
        from zutomayo.data.database import get_pool

        async with get_pool().acquire() as connection:
            rows = await connection.fetch(
                '''
                SELECT display_names.user_id, display_names.name
                FROM display_names
                JOIN player_profiles ON player_profiles.user_id = display_names.user_id
                WHERE lower(display_names.name) LIKE lower($1) || '%'
                ORDER BY display_names.name, display_names.user_id
                LIMIT $2
                ''',
                prefix, limit,
            )
        return [(row['user_id'], row['name']) for row in rows]


backend = PostgresNameBackend()


async def load_display_name_cache() -> None:
    """Fill the in-memory cache from storage. Runs once at startup (main.py setup_hook)."""
    global _names_cache
    _names_cache = await backend.load_all()
    log.info('Loaded %d display names', len(_names_cache))


def _cache() -> dict[str, dict]:
    global _names_cache
    if _names_cache is None:
        # Before startup finishes (or in tests that never load), start empty;
        # writes still reach the backend so nothing is lost.
        _names_cache = {}
    return _names_cache


def _schedule_persist(user_id: int, name: str, custom: bool) -> None:
    """
    Persist a cache write without an await point (used by synchronous capture
    paths). Outside a running event loop the write stays cache-only; the next
    interaction re-captures and persists it.
    """
    async def persist() -> None:
        try:
            await backend.upsert(user_id, name, custom)
        except Exception:
            log.exception('Failed to persist display name for user %s', user_id)

    try:
        asyncio.get_running_loop().create_task(persist())
    except RuntimeError:
        pass


def remember_user(user_id: int, display_name: str) -> None:
    """Record an automatically captured name. No-op if unchanged or overridden by a custom name."""
    if not display_name:
        return
    cache = _cache()
    entry = cache.get(str(user_id))
    if entry is not None and (entry.get('custom') or entry.get('name') == display_name):
        return
    cache[str(user_id)] = {'name': display_name, 'custom': False}
    _schedule_persist(user_id, display_name, False)


async def set_custom_name(user_id: int, display_name: str) -> None:
    """Set a user-chosen name that automatic capture will never overwrite."""
    _cache()[str(user_id)] = {'name': display_name, 'custom': True}
    await backend.upsert(user_id, display_name, True)


async def clear_custom_name(user_id: int) -> None:
    """Drop any stored entry so the next interaction re-captures the Discord name."""
    if _cache().pop(str(user_id), None) is not None:
        await backend.delete(user_id)


def get_stored_display_name(user_id: int) -> Optional[str]:
    entry = _cache().get(str(user_id))
    return entry.get('name') if entry is not None else None


def _short_user_fallback(user_id: int) -> str:
    return f'User#{str(user_id)[-4:]}'


def resolve_display_name(bot: Optional[discord.Client], user_id: int) -> str:
    """
    Best-effort display name without privileged intents.

    Order: custom name set via editname; live user cache (and remember the result);
    previously captured name; 'User#NNNN' fallback from the ID's last digits.
    """
    entry = _cache().get(str(user_id))
    if entry is not None and entry.get('custom'):
        return entry['name']

    if bot is not None:
        user = bot.get_user(user_id)
        if user is not None:
            live_name = user.global_name or user.name
            remember_user(user_id, live_name)
            return live_name

    if entry is not None:
        return entry['name']
    return _short_user_fallback(user_id)


async def search_known_players(prefix: str, limit: int = 25) -> list[tuple[int, str]]:
    """Autocomplete source for player search options."""
    return await backend.search_known_players(prefix, limit)


async def ensure_display_names(bot: discord.Client, user_ids: Iterable[int]) -> None:
    """
    Backfill names for users with no stored entry and no cache hit by fetching
    them over REST (no intent required). Failures (deleted accounts, API errors)
    are logged and skipped so rendering can proceed with fallbacks.
    """
    for user_id in set(user_ids):
        if get_stored_display_name(user_id) is not None or bot.get_user(user_id) is not None:
            continue
        try:
            user = await bot.fetch_user(user_id)
        except discord.NotFound:
            log.info('User %s no longer exists; skipping name backfill', user_id)
            continue
        except discord.HTTPException as error:
            log.warning('Failed to fetch user %s for name backfill: %s', user_id, error)
            continue
        remember_user(user_id, user.global_name or user.name)
