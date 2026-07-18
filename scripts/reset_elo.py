"""
Reset every player's ELO for one ladder to the starting rating.

Usage:
    python scripts/reset_elo.py --format standard
    python scripts/reset_elo.py --format tcg

Iterates all profiles in the database and resets the three ELO fields of the
chosen ladder (rating, peak, games played). Lifetime win/loss/draw stats,
deck stats, and opponent stats are left untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from zutomayo.data import database
from zutomayo.data.player_storage import (
    ELO_STARTING_RATING,
    list_all_profiles,
    save_profile,
)

ELO_FIELDS_BY_FORMAT = {
    'standard': ('elo', 'elo_peak', 'elo_games'),
    'tcg': ('tcg_elo', 'tcg_elo_peak', 'tcg_elo_games'),
}


async def reset_elo(game_format: str) -> None:
    rating_field, peak_field, games_field = ELO_FIELDS_BY_FORMAT[game_format]
    await database.initialize_pool()
    try:
        reset_count = 0
        for profile in await list_all_profiles():
            profile[rating_field] = ELO_STARTING_RATING
            profile[peak_field] = ELO_STARTING_RATING
            profile[games_field] = 0
            await save_profile(profile['user_id'], profile)
            reset_count += 1
        print(f'{game_format} ELO reset for {reset_count} profile(s).')
    finally:
        await database.close_pool()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Reset one ladder's ELO for all players.")
    parser.add_argument(
        '--format',
        dest='game_format',
        required=True,
        choices=sorted(ELO_FIELDS_BY_FORMAT),
        help='Which ELO ladder to reset.',
    )
    arguments = parser.parse_args()
    asyncio.run(reset_elo(arguments.game_format))


if __name__ == '__main__':
    main()
