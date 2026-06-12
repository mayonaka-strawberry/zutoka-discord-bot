"""
Persistence for Discord user display names, keyed by user ID.

Interactions always include the acting user's name regardless of gateway
intents, so the bot captures names there (see GameCog.capture_interaction_user_name)
instead of relying on the member cache, which is empty now that the privileged
members intent is no longer requested.

Stored in JSON at zutomayo/players/usernames.json as
{"<user_id>": {"name": str, "custom": bool}}. Entries with custom=True were set
via /zutomayo editname and are never overwritten by automatic capture.
Writes are atomic via temp-file + os.replace.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable, Optional

import discord

from zutomayo.data.player_storage import PLAYERS_DIRECTORY


log = logging.getLogger(__name__)


USERNAMES_FILE = PLAYERS_DIRECTORY / 'usernames.json'

MAXIMUM_CUSTOM_NAME_LENGTH = 32

_names_cache: Optional[dict[str, dict]] = None


def _load_cache() -> dict[str, dict]:
    global _names_cache
    if _names_cache is not None:
        return _names_cache
    if USERNAMES_FILE.exists():
        try:
            with open(USERNAMES_FILE, 'r', encoding='utf-8') as file_handle:
                _names_cache = json.load(file_handle)
        except (json.JSONDecodeError, OSError) as error:
            log.exception('Failed to load usernames file: %s', error)
            _names_cache = {}
    else:
        _names_cache = {}
    return _names_cache


def _save_cache() -> None:
    PLAYERS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temp_path = USERNAMES_FILE.with_suffix('.json.tmp')
    with open(temp_path, 'w', encoding='utf-8') as file_handle:
        json.dump(_load_cache(), file_handle, indent=2, ensure_ascii=False)
    os.replace(temp_path, USERNAMES_FILE)


def remember_user(user_id: int, display_name: str) -> None:
    """Record an automatically captured name. No-op if unchanged or overridden by a custom name."""
    if not display_name:
        return
    cache = _load_cache()
    entry = cache.get(str(user_id))
    if entry is not None and (entry.get('custom') or entry.get('name') == display_name):
        return
    cache[str(user_id)] = {'name': display_name, 'custom': False}
    _save_cache()


def set_custom_name(user_id: int, display_name: str) -> None:
    """Set a user-chosen name that automatic capture will never overwrite."""
    cache = _load_cache()
    cache[str(user_id)] = {'name': display_name, 'custom': True}
    _save_cache()


def clear_custom_name(user_id: int) -> None:
    """Drop any stored entry so the next interaction re-captures the Discord name."""
    cache = _load_cache()
    if cache.pop(str(user_id), None) is not None:
        _save_cache()


def get_stored_display_name(user_id: int) -> Optional[str]:
    entry = _load_cache().get(str(user_id))
    return entry.get('name') if entry is not None else None


def _short_user_fallback(user_id: int) -> str:
    return f'User#{str(user_id)[-4:]}'


def resolve_display_name(bot: Optional[discord.Client], user_id: int) -> str:
    """
    Best-effort display name without privileged intents.

    Order: custom name set via editname; live user cache (and remember the result);
    previously captured name; 'User#NNNN' fallback from the ID's last digits.
    """
    cache = _load_cache()
    entry = cache.get(str(user_id))
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
