"""
Embed builders for player-management commands.

Style constraint: no emojis, no decorative symbols. Plain text only.
W-L only — draws are tracked in the data but never shown in any embed.
"""

from __future__ import annotations

from typing import Optional

import discord


def _format_win_rate(wins: int, losses: int) -> str:
    """Win-rate over decided games only. Draws excluded from both numerator and denominator."""
    decided = wins + losses
    if decided == 0:
        return '—'
    return f'{(wins / decided) * 100:.1f}%'


def _format_record(wins: int, losses: int) -> str:
    return f'{wins}W – {losses}L'


def _aggregate_pvp_record(stats: dict) -> tuple[int, int]:
    """Combined PvP wins/losses across standard + tcg_match. Used for the headline 'PvP Record' field."""
    standard = stats.get('standard', {})
    tcg_match = stats.get('tcg_match', {})
    wins = standard.get('wins', 0) + tcg_match.get('wins', 0)
    losses = standard.get('losses', 0) + tcg_match.get('losses', 0)
    return wins, losses


def _top_decks(deck_stats: dict, limit: int = 3) -> list[tuple[str, str, int, int, int]]:
    """
    Return up to `limit` decks, sorted by total games played desc.
    Each tuple is (deck_name, format, total_games, total_wins, total_losses).
    Combines pvp and solo games for the 'games played' sort, but reports W-L from pvp+solo combined.
    """
    flat: list[tuple[str, str, int, int, int]] = []
    for deck_format, name_to_entry in deck_stats.items():
        for deck_name, entry in name_to_entry.items():
            pvp = entry.get('pvp', {})
            solo = entry.get('solo', {})
            wins = pvp.get('wins', 0) + solo.get('wins', 0)
            losses = pvp.get('losses', 0) + solo.get('losses', 0)
            draws = pvp.get('draws', 0) + solo.get('draws', 0)
            total_games = wins + losses + draws
            if total_games == 0:
                continue
            flat.append((deck_name, deck_format, total_games, wins, losses))
    flat.sort(key=lambda row: row[2], reverse=True)
    return flat[:limit]


def _top_rivals(opponent_stats: dict, limit: int = 10) -> list[tuple[str, int, int, int]]:
    """
    Return up to `limit` rivals sorted by games desc, then last_played desc.
    Each tuple is (opponent_id_str, games, wins, losses).
    """
    flat: list[tuple[str, int, int, int, str]] = []
    for opponent_id_str, entry in opponent_stats.items():
        games = entry.get('games', 0)
        if games == 0:
            continue
        flat.append((
            opponent_id_str,
            games,
            entry.get('wins', 0),
            entry.get('losses', 0),
            entry.get('last_played') or '',
        ))
    flat.sort(key=lambda row: (row[1], row[4]), reverse=True)
    return [(row[0], row[1], row[2], row[3]) for row in flat[:limit]]


def _short_user_fallback(user_id: int) -> str:
    return f'User#{str(user_id)[-4:]}'


def _resolve_username(bot: discord.Client, user_id: int) -> str:
    user = bot.get_user(user_id)
    if user is not None:
        return user.display_name
    return _short_user_fallback(user_id)


def build_profile_embed(
    bot: discord.Client,
    member: discord.abc.User,
    profile: dict,
) -> discord.Embed:
    stats = profile.get('stats', {})

    pvp_wins, pvp_losses = _aggregate_pvp_record(stats)
    total_decided = pvp_wins + pvp_losses
    if total_decided == 0 and sum(
        stats.get(bucket, {}).get('wins', 0) + stats.get(bucket, {}).get('losses', 0)
        for bucket in ('solo_easy', 'solo_normal')
    ) == 0:
        embed = discord.Embed(
            title=f"Your Profile — {member.display_name}",
            description='No games played yet. Start with `/zutomayo create` or `/zutomayo playuniguri`.',
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    embed = discord.Embed(
        title=f"Your Profile — {member.display_name}",
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    elo = profile.get('elo', 1000)
    elo_peak = profile.get('elo_peak', 1000)
    tcg_elo = profile.get('tcg_elo', 1000)
    tcg_elo_peak = profile.get('tcg_elo_peak', 1000)
    embed.add_field(
        name='Elo Rating',
        value=f'{elo} (peak {elo_peak})',
        inline=True,
    )
    embed.add_field(
        name='TCG Elo Rating',
        value=f'{tcg_elo} (peak {tcg_elo_peak})',
        inline=True,
    )
    embed.add_field(
        name='PvP Record',
        value=_format_record(pvp_wins, pvp_losses),
        inline=True,
    )
    embed.add_field(
        name='PvP Win Rate',
        value=_format_win_rate(pvp_wins, pvp_losses),
        inline=True,
    )

    standard = stats.get('standard', {})
    tcg_match = stats.get('tcg_match', {})
    tcg_series = stats.get('tcg_series', {})
    embed.add_field(
        name='Standard',
        value=_format_record(standard.get('wins', 0), standard.get('losses', 0)),
        inline=True,
    )
    embed.add_field(
        name='TCG Match',
        value=_format_record(tcg_match.get('wins', 0), tcg_match.get('losses', 0)),
        inline=True,
    )
    embed.add_field(
        name='TCG Series',
        value=_format_record(tcg_series.get('wins', 0), tcg_series.get('losses', 0)),
        inline=True,
    )

    solo_normal = stats.get('solo_normal', {})
    solo_easy = stats.get('solo_easy', {})
    forfeits_given = stats.get('forfeits_given', 0)
    forfeits_received = stats.get('forfeits_received', 0)
    embed.add_field(
        name='Solo (Normal)',
        value=_format_record(solo_normal.get('wins', 0), solo_normal.get('losses', 0)),
        inline=True,
    )
    embed.add_field(
        name='Solo (Easy)',
        value=_format_record(solo_easy.get('wins', 0), solo_easy.get('losses', 0)),
        inline=True,
    )
    embed.add_field(
        name='Forfeits',
        value=f'Given {forfeits_given} / Recv. {forfeits_received}',
        inline=True,
    )

    top_decks = _top_decks(profile.get('deck_stats', {}))
    if top_decks:
        lines = []
        for index, (deck_name, deck_format, total_games, wins, losses) in enumerate(top_decks, start=1):
            win_rate = _format_win_rate(wins, losses)
            format_suffix = f' [{deck_format}]' if deck_format == 'tcg' else ''
            lines.append(
                f'{index}. {deck_name}{format_suffix}  —  {total_games} games  ({wins}-{losses}, {win_rate})'
            )
        embed.add_field(name='Top Decks', value='\n'.join(lines), inline=False)

    top_rivals = _top_rivals(profile.get('opponent_stats', {}))
    if top_rivals:
        lines = []
        for index, (opponent_id_str, games, wins, losses) in enumerate(top_rivals, start=1):
            try:
                opponent_id = int(opponent_id_str)
            except (TypeError, ValueError):
                continue
            name = _resolve_username(bot, opponent_id)
            lines.append(f'{index}. {name}  —  {games} games   {_format_record(wins, losses)}')
        if lines:
            embed.add_field(
                name=f'Top {len(lines)} Opponents',
                value='\n'.join(lines),
                inline=False,
            )

    embed.set_footer(text='Renamed/deleted decks keep their lifetime stats.')
    return embed


def build_leaderboard_embed(
    bot: discord.Client,
    ranked_rows: list[dict],
    caller_id: int,
    *,
    title: str = 'Zutoka Leaderboard',
    elo_field: str = 'elo',
    elo_games_field: str = 'elo_games',
    record_stats_bucket: str = 'standard',
    empty_message: str = 'No ranked players yet. Play a standard PvP game to appear here.',
) -> discord.Embed:
    """
    ranked_rows: profiles already filtered/sorted by the caller. This builder only renders.
    Top 10 rendered; if the caller is below rank 10, an extra 'Your rank' line is appended.

    The same renderer drives both /zutomayo leaderboard (standard Elo) and
    /zutomayo leaderboardtcg (TCG Elo) — the caller picks the rating field, the
    W-L bucket to show beside each entry, and the empty/title strings.
    """
    embed = discord.Embed(
        title=title,
        color=discord.Color.gold(),
    )

    if not ranked_rows:
        embed.add_field(
            name='​',
            value=empty_message,
            inline=False,
        )
        return embed

    visible_rows = ranked_rows[:10]

    caller_rank: Optional[int] = None
    for rank_index, row in enumerate(ranked_rows, start=1):
        if row['user_id'] == caller_id:
            caller_rank = rank_index
            break

    lines = []
    for rank_index, row in enumerate(visible_rows, start=1):
        stats = row.get('stats', {})
        record_bucket = stats.get(record_stats_bucket, {})
        record_wins = record_bucket.get('wins', 0)
        record_losses = record_bucket.get('losses', 0)
        name = _resolve_username(bot, row['user_id'])
        suffix = ' (you)' if row['user_id'] == caller_id else ''
        elo = row.get(elo_field, 1000)
        elo_games = row.get(elo_games_field, 0)
        lines.append(
            f'{rank_index}. {name}{suffix}  —  {elo} Elo  '
            f'({record_wins}W – {record_losses}L, {elo_games} games)'
        )

    embed.add_field(
        name='​',
        value='\n'.join(lines),
        inline=False,
    )

    if caller_rank is not None and caller_rank > len(visible_rows):
        caller_row = next(row for row in ranked_rows if row['user_id'] == caller_id)
        embed.add_field(
            name='​',
            value=f'Your rank: #{caller_rank} ({caller_row.get(elo_field, 1000)} Elo)',
            inline=False,
        )

    return embed
