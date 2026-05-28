"""Reset every player's TCG-series ELO to the starting rating.

Iterates all profiles in zutomayo/players/ and resets the three TCG-ELO fields
(rating, peak, games played). Lifetime win/loss/draw stats, deck stats, and
opponent stats are left untouched.

Usage:
    python reset_tcg_elo.py
"""

from __future__ import annotations

from zutomayo.data.player_storage import (
    ELO_STARTING_RATING,
    iter_all_profiles,
    save_profile,
)


def main() -> None:
    reset_count = 0
    for profile in iter_all_profiles():
        profile['tcg_elo'] = ELO_STARTING_RATING
        profile['tcg_elo_peak'] = ELO_STARTING_RATING
        profile['tcg_elo_games'] = 0
        save_profile(profile['user_id'], profile)
        reset_count += 1
    print(f'TCG ELO reset for {reset_count} profile(s).')


if __name__ == '__main__':
    main()
