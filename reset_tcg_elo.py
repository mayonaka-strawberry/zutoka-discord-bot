"""Reset every player's TCG-series ELO to the starting rating.

Iterates all profiles in the database and resets the three TCG-ELO fields
(rating, peak, games played). Lifetime win/loss/draw stats, deck stats, and
opponent stats are left untouched.

Usage:
    python reset_tcg_elo.py
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from zutomayo.data import database
from zutomayo.data.player_storage import (
    ELO_STARTING_RATING,
    list_all_profiles,
    save_profile,
)


async def reset_tcg_elo() -> None:
    await database.initialize_pool()
    try:
        reset_count = 0
        for profile in await list_all_profiles():
            profile['tcg_elo'] = ELO_STARTING_RATING
            profile['tcg_elo_peak'] = ELO_STARTING_RATING
            profile['tcg_elo_games'] = 0
            await save_profile(profile['user_id'], profile)
            reset_count += 1
        print(f'TCG ELO reset for {reset_count} profile(s).')
    finally:
        await database.close_pool()


if __name__ == '__main__':
    load_dotenv()
    asyncio.run(reset_tcg_elo())
