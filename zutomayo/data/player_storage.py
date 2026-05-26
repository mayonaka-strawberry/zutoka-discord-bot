"""
Persistence layer for per-user player profiles (Elo rating, win/loss records,
per-deck stats, per-opponent stats, forfeits).

Each user's profile is stored in JSON at zutomayo/players/<discord_user_id>.json.
Writes are atomic via temp-file + os.replace.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


log = logging.getLogger(__name__)


PLAYERS_DIRECTORY = Path(__file__).resolve().parent.parent / 'players'

ELO_STARTING_RATING = 1000
ELO_K_FACTOR = 32

BOT_DISCORD_ID = 0  # sentinel; matches GameSessionManager.BOT_DISCORD_ID


def _profile_file(user_id: int) -> Path:
    return PLAYERS_DIRECTORY / f'{user_id}.json'


def _empty_profile(user_id: int) -> dict:
    return {
        'user_id': user_id,
        'last_updated': None,
        'elo': ELO_STARTING_RATING,
        'elo_peak': ELO_STARTING_RATING,
        'elo_games': 0,
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


def load_profile(user_id: int) -> dict:
    """Load a user's profile. Returns a fresh empty-default profile if the file is missing."""
    path = _profile_file(user_id)
    if not path.exists():
        return _empty_profile(user_id)
    try:
        with open(path, 'r', encoding='utf-8') as file_handle:
            data = json.load(file_handle)
    except (json.JSONDecodeError, OSError) as error:
        log.exception('Failed to load profile for user %s: %s', user_id, error)
        return _empty_profile(user_id)
    return _migrate_profile(data, user_id)


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


def save_profile(user_id: int, profile: dict) -> None:
    """Write a profile atomically (temp + os.replace) to avoid corruption."""
    PLAYERS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    profile['last_updated'] = _now_iso()
    final_path = _profile_file(user_id)
    temp_path = final_path.with_suffix('.json.tmp')
    with open(temp_path, 'w', encoding='utf-8') as file_handle:
        json.dump(profile, file_handle, indent=2)
    os.replace(temp_path, final_path)


def iter_all_profiles() -> Iterable[dict]:
    """Yield every saved profile. Used by the leaderboard."""
    if not PLAYERS_DIRECTORY.exists():
        return
    for path in PLAYERS_DIRECTORY.glob('*.json'):
        try:
            with open(path, 'r', encoding='utf-8') as file_handle:
                data = json.load(file_handle)
        except (json.JSONDecodeError, OSError) as error:
            log.warning('Skipping unreadable profile %s: %s', path, error)
            continue
        user_id = data.get('user_id')
        if user_id is None:
            continue
        yield _migrate_profile(data, int(user_id))


def _expected_score(rating_a: int, rating_b: int) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def _apply_elo_update(profile_a: dict, profile_b: dict, score_a: float) -> None:
    """
    Update Elo on both profiles. score_a is 1.0 (a wins), 0.5 (draw), or 0.0 (b wins).
    Standard Elo formula: bigger rating gaps already produce asymmetric swings (small for
    favourites, large for upsets) without any custom K-factor tweaks.
    """
    rating_a = profile_a.get('elo', ELO_STARTING_RATING)
    rating_b = profile_b.get('elo', ELO_STARTING_RATING)
    expected_a = _expected_score(rating_a, rating_b)
    delta_a = round(ELO_K_FACTOR * (score_a - expected_a))

    profile_a['elo'] = rating_a + delta_a
    profile_b['elo'] = rating_b - delta_a
    profile_a['elo_peak'] = max(profile_a.get('elo_peak', ELO_STARTING_RATING), profile_a['elo'])
    profile_b['elo_peak'] = max(profile_b.get('elo_peak', ELO_STARTING_RATING), profile_b['elo'])
    profile_a['elo_games'] = profile_a.get('elo_games', 0) + 1
    profile_b['elo_games'] = profile_b.get('elo_games', 0) + 1


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


def record_match_result(
    player_zero_id: int,
    player_one_id: int,
    player_zero_deck_name: Optional[str],
    player_one_deck_name: Optional[str],
    winner_index_or_none: Optional[int],
    *,
    mode: str,
    is_solo: bool,
    solo_difficulty: str = 'normal',
) -> None:
    """
    Record one match's result.

    mode is 'standard' (PvP non-TCG, or solo) or 'tcg_match' (one game inside a TCG series).
    For solo, only the human player's profile is updated and only solo_* counters change.
    For PvP, both profiles are updated: top-level stats, opponent_stats, deck_stats, and Elo.
    """
    if is_solo:
        human_id = player_zero_id if player_zero_id != BOT_DISCORD_ID else player_one_id
        bot_index = 1 if player_zero_id != BOT_DISCORD_ID else 0
        human_index = 1 - bot_index
        human_deck_name = player_zero_deck_name if human_index == 0 else player_one_deck_name

        profile = load_profile(human_id)
        solo_bucket = 'solo_easy' if solo_difficulty == 'easy' else 'solo_normal'
        outcome_key, _ = _outcome_keys(human_index, winner_index_or_none)
        profile['stats'][solo_bucket][outcome_key] += 1

        deck_format = 'standard'  # solo always uses the standard format engine
        deck_entry = _ensure_deck_entry(profile, deck_format, human_deck_name)
        deck_entry['solo']['games'] += 1
        deck_entry['solo'][outcome_key] += 1

        save_profile(human_id, profile)
        return

    # PvP path: both profiles change.
    profile_zero = load_profile(player_zero_id)
    profile_one  = load_profile(player_one_id)

    outcome_zero, score_zero = _outcome_keys(0, winner_index_or_none)
    outcome_one,  _          = _outcome_keys(1, winner_index_or_none)

    stats_bucket = 'tcg_match' if mode == 'tcg_match' else 'standard'
    profile_zero['stats'][stats_bucket][outcome_zero] += 1
    profile_one['stats'][stats_bucket][outcome_one] += 1

    deck_format = 'tcg' if mode == 'tcg_match' else 'standard'
    deck_entry_zero = _ensure_deck_entry(profile_zero, deck_format, player_zero_deck_name)
    deck_entry_one  = _ensure_deck_entry(profile_one,  deck_format, player_one_deck_name)
    deck_entry_zero['pvp']['games'] += 1
    deck_entry_zero['pvp'][outcome_zero] += 1
    deck_entry_one['pvp']['games'] += 1
    deck_entry_one['pvp'][outcome_one] += 1

    opponent_entry_zero = _ensure_opponent_entry(profile_zero, player_one_id)
    opponent_entry_one  = _ensure_opponent_entry(profile_one,  player_zero_id)
    timestamp = _now_iso()
    for entry, outcome_key in ((opponent_entry_zero, outcome_zero), (opponent_entry_one, outcome_one)):
        entry['games'] += 1
        entry[outcome_key] += 1
        entry['last_played'] = timestamp

    # Elo is intentionally scoped to standard PvP only — TCG matches are tracked but do
    # not move the rating used by the leaderboard.
    if mode == 'standard':
        _apply_elo_update(profile_zero, profile_one, score_zero)

    save_profile(player_zero_id, profile_zero)
    save_profile(player_one_id, profile_one)


def record_tcg_series(
    player_zero_id: int,
    player_one_id: int,
    wins_dict: dict,
) -> None:
    """Record series-level TCG result. Per-match Elo and per-match stats were handled during the series."""
    if BOT_DISCORD_ID in (player_zero_id, player_one_id):
        return  # series-level recording is PvP only

    profile_zero = load_profile(player_zero_id)
    profile_one  = load_profile(player_one_id)

    wins_zero = wins_dict.get(0, 0)
    wins_one  = wins_dict.get(1, 0)
    if wins_zero > wins_one:
        profile_zero['stats']['tcg_series']['wins']   += 1
        profile_one['stats']['tcg_series']['losses']  += 1
    elif wins_one > wins_zero:
        profile_one['stats']['tcg_series']['wins']    += 1
        profile_zero['stats']['tcg_series']['losses'] += 1
    else:
        return  # tied series shouldn't happen, but skip the write rather than guessing

    save_profile(player_zero_id, profile_zero)
    save_profile(player_one_id, profile_one)


def record_forfeit(quitter_id: int, opponent_id_or_none: Optional[int]) -> None:
    """
    Forfeit counter only. Does NOT affect Elo or win/loss columns.
    opponent_id_or_none is None for solo (bot opponent) — only the quitter's profile updates.
    """
    if quitter_id == BOT_DISCORD_ID:
        return
    quitter_profile = load_profile(quitter_id)
    quitter_profile['stats']['forfeits_given'] += 1
    save_profile(quitter_id, quitter_profile)

    if opponent_id_or_none is None or opponent_id_or_none == BOT_DISCORD_ID:
        return
    opponent_profile = load_profile(opponent_id_or_none)
    opponent_profile['stats']['forfeits_received'] += 1
    save_profile(opponent_id_or_none, opponent_profile)


def record_standard_pvp_quit(quitter_id: int, opponent_id: int) -> None:
    """
    Standard-PvP quit handling: forfeit counters PLUS a half-loss Elo penalty.

    Applies the symmetric Elo delta equal to half the magnitude a full loss
    (score=0.0) would produce against this opponent, rounded to an integer.
    Win/loss columns are intentionally untouched (they remain integer-only;
    a quit is recorded as a forfeit, not as a fractional loss).

    Caller is responsible for ensuring this is invoked only for standard PvP
    games (non-TCG, non-solo) where a human opponent exists.
    """
    if quitter_id == BOT_DISCORD_ID or opponent_id == BOT_DISCORD_ID:
        return

    quitter_profile = load_profile(quitter_id)
    opponent_profile = load_profile(opponent_id)

    quitter_profile['stats']['forfeits_given'] += 1
    opponent_profile['stats']['forfeits_received'] += 1

    rating_quitter = quitter_profile.get('elo', ELO_STARTING_RATING)
    rating_opponent = opponent_profile.get('elo', ELO_STARTING_RATING)
    expected_quitter = _expected_score(rating_quitter, rating_opponent)
    full_loss_delta = round(ELO_K_FACTOR * (0.0 - expected_quitter))
    half_loss_delta = round(full_loss_delta / 2)

    quitter_profile['elo'] = rating_quitter + half_loss_delta
    opponent_profile['elo'] = rating_opponent - half_loss_delta
    quitter_profile['elo_peak'] = max(
        quitter_profile.get('elo_peak', ELO_STARTING_RATING),
        quitter_profile['elo'],
    )
    opponent_profile['elo_peak'] = max(
        opponent_profile.get('elo_peak', ELO_STARTING_RATING),
        opponent_profile['elo'],
    )
    quitter_profile['elo_games'] = quitter_profile.get('elo_games', 0) + 1
    opponent_profile['elo_games'] = opponent_profile.get('elo_games', 0) + 1

    save_profile(quitter_id, quitter_profile)
    save_profile(opponent_id, opponent_profile)
