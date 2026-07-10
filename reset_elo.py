"""Reset every player's standard PvP ELO to the starting rating.

Iterates all profiles in the database and resets the three standard-ELO
fields (rating, peak, games played). Lifetime win/loss/draw stats, deck stats,
and opponent stats are left untouched.

Usage:
    python reset_elo.py
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


async def reset_standard_elo() -> None:
    await database.initialize_pool()
    try:
        reset_count = 0
        for profile in await list_all_profiles():
            profile['elo'] = ELO_STARTING_RATING
            profile['elo_peak'] = ELO_STARTING_RATING
            profile['elo_games'] = 0
            await save_profile(profile['user_id'], profile)
            reset_count += 1
        print(f'Standard ELO reset for {reset_count} profile(s).')
    finally:
        await database.close_pool()


if __name__ == '__main__':
    load_dotenv()
    asyncio.run(reset_standard_elo())
