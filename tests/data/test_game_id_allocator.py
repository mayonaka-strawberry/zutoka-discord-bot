"""Unit tests for game id allocation: format, per-day counters, UTC rollover."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.data.game_id_allocator import GAME_ID_PATTERN, allocate_game_id  # noqa: E402


def test_first_allocation_of_a_day_is_counter_zero():
    moment = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    game_id = asyncio.run(allocate_game_id(moment))
    assert game_id == '20260710-00000'
    assert GAME_ID_PATTERN.fullmatch(game_id)


def test_sequential_allocations_increment_within_a_day():
    moment = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

    async def allocate_three():
        return [await allocate_game_id(moment) for _ in range(3)]

    assert asyncio.run(allocate_three()) == [
        '20260710-00000', '20260710-00001', '20260710-00002',
    ]


def test_counter_resets_on_utc_day_rollover():
    async def allocate_across_days():
        before_midnight = datetime(2026, 7, 10, 23, 59, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 7, 11, 0, 1, tzinfo=timezone.utc)
        return (
            await allocate_game_id(before_midnight),
            await allocate_game_id(after_midnight),
        )

    last_of_day, first_of_next = asyncio.run(allocate_across_days())
    assert last_of_day == '20260710-00000'
    assert first_of_next == '20260711-00000'


def test_date_prefix_uses_utc_not_local_time():
    # 2026-07-10 23:30 in UTC-9 is already 2026-07-11 in UTC.
    from datetime import timedelta, timezone as timezone_module

    local_zone = timezone_module(timedelta(hours=-9))
    moment = datetime(2026, 7, 11, 8, 30, tzinfo=timezone.utc).astimezone(local_zone)
    game_id = asyncio.run(allocate_game_id(moment))
    assert game_id.startswith('20260711-')
