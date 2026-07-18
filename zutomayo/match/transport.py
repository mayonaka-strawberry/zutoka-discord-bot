"""
MatchTransport: the output side of a match, behind one interface.

A transport delivers game messages (text, embeds, board images) to players and
the originating channel. Flow code and the effect engine send exclusively
through the session's transport so the same game logic runs over Discord DMs,
headless test recorders, or a muted replay (during resume after a restart,
``muted`` is True and every send is a no-op).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    import discord
    from zutomayo.engine.game_session import GameSession

log = logging.getLogger(__name__)

# Sentinel Discord ID used for the bot player in solo mode.
BOT_SENTINEL_DISCORD_ID = 0


class MatchTransport(Protocol):
    muted: bool

    async def send_to_player(self, session: 'GameSession', player_index: int, **kwargs: Any) -> Optional['discord.Message']:
        ...

    async def send_to_channel(self, session: 'GameSession', **kwargs: Any) -> Optional['discord.Message']:
        ...

    def display_name(self, session: 'GameSession', player_index: int) -> Optional[str]:
        ...

    def delivers_to_player(self, session: 'GameSession', player_index: int) -> bool:
        """Whether a DM to this player would actually be delivered. Flows use
        this to skip rendering board and hand images no one will receive
        (the solo-mode bot, or everything while muted during replay)."""
        ...


class DiscordMatchTransport:
    """Sends over Discord DMs and the match's channel, exactly as the flows and
    effect engine did before the transport existed (same retry helper and labels).
    DMs addressed to the solo-mode bot player (sentinel Discord ID 0) are skipped."""

    def __init__(self, bot: 'discord.Client') -> None:
        self.bot = bot
        self.muted = False

    async def _get_dm_channel(self, discord_id: int) -> 'discord.DMChannel':
        from zutomayo.utils.discord_utils import send_with_retry

        user = self.bot.get_user(discord_id)
        if user is None:
            user = await send_with_retry(lambda: self.bot.fetch_user(discord_id), label='fetch_user')
        return await send_with_retry(lambda: user.create_dm(), label='create_dm')

    async def send_to_player(self, session: 'GameSession', player_index: int, **kwargs: Any) -> Optional['discord.Message']:
        from zutomayo.utils.discord_utils import send_with_retry

        if self.muted:
            return None
        discord_id = session.get_discord_id(player_index)
        if discord_id is None or discord_id == BOT_SENTINEL_DISCORD_ID:
            return None
        dm_channel = await self._get_dm_channel(discord_id)
        return await send_with_retry(lambda: dm_channel.send(**kwargs), label='DM send')

    async def send_to_channel(self, session: 'GameSession', **kwargs: Any) -> Optional['discord.Message']:
        from zutomayo.utils.discord_utils import send_with_retry

        if self.muted:
            return None
        self._record_narration(session, kwargs)
        channel = self.bot.get_channel(session.channel_id)
        if channel is None:
            return None
        return await send_with_retry(lambda: channel.send(**kwargs), label='channel send')

    @staticmethod
    def _record_narration(session: 'GameSession', kwargs: dict[str, Any]) -> None:
        """Mirror channel narration into the game event stream. Runs before the
        channel lookup so solo games (no guild channel) are recorded too;
        muted sends never reach here, so replay stays silent."""
        if session.persistence is None:
            return
        embeds = list(kwargs.get('embeds') or [])
        if kwargs.get('embed') is not None:
            embeds.insert(0, kwargs['embed'])
        payload = {
            'content': kwargs.get('content'),
            'embeds': [
                {'title': embed.title, 'description': embed.description}
                for embed in embeds
            ],
        }
        if payload['content'] is None and not payload['embeds']:
            return
        from zutomayo.engine.game_events import EVENT_NARRATION

        session.persistence.emit_event(EVENT_NARRATION, payload)

    def display_name(self, session: 'GameSession', player_index: int) -> Optional[str]:
        from zutomayo.data.name_storage import resolve_display_name
        from zutomayo.match.agents import BOT_NAME

        discord_id = session.get_discord_id(player_index)
        if discord_id is None:
            return None
        if discord_id == BOT_SENTINEL_DISCORD_ID:
            return BOT_NAME
        return resolve_display_name(self.bot, discord_id)

    def delivers_to_player(self, session: 'GameSession', player_index: int) -> bool:
        if self.muted:
            return False
        discord_id = session.get_discord_id(player_index)
        return discord_id is not None and discord_id != BOT_SENTINEL_DISCORD_ID
