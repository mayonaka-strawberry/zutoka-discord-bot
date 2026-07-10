"""
Game id allocation: YYYYMMDD-NNNNN, where the date is the current UTC day and
the counter starts at 00000 for each day's first game. Allocation is atomic
through a single upsert on the daily_game_counters table, so concurrent game
creations can never collide.

The allocator is swappable through the module-level `backend` attribute;
tests install a sequential in-memory allocator.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

GAME_ID_PATTERN = re.compile(r'^\d{8}-\d{5,}$')

ALLOCATION_SQL = (
    'INSERT INTO daily_game_counters (day, next_counter) VALUES ($1, 1) '
    'ON CONFLICT (day) DO UPDATE '
    'SET next_counter = daily_game_counters.next_counter + 1 '
    'RETURNING next_counter - 1'
)


def format_game_id(day: date, counter: int) -> str:
    return f'{day:%Y%m%d}-{counter:05d}'


def _current_utc_day(now: Optional[datetime]) -> date:
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).date()


class PostgresGameIdAllocator:
    async def allocate(self, now: Optional[datetime] = None) -> str:
        from zutomayo.data.database import get_pool

        day = _current_utc_day(now)
        async with get_pool().acquire() as connection:
            counter = await connection.fetchval(ALLOCATION_SQL, day)
        return format_game_id(day, counter)


backend = PostgresGameIdAllocator()


async def allocate_game_id(now: Optional[datetime] = None) -> str:
    """Allocate the next game id for the current (or given) UTC day."""
    return await backend.allocate(now)
