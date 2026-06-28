"""
Pagination view for the leaderboard commands.

Drives both /zutomayo leaderboard and /zutomayo leaderboardtcg: the cog passes the
already filtered/sorted rows plus the renderer kwargs, and this view rebuilds any
page on demand. Anyone in the channel may page; the buttons disable after the view
times out.

Style constraint: no emojis, no decorative symbols. Plain text only.
"""

from __future__ import annotations

import logging
import math

import discord

from zutomayo.data.name_storage import ensure_display_names
from zutomayo.ui.player_embeds import build_leaderboard_embed


log = logging.getLogger(__name__)

LEADERBOARD_PAGE_SIZE = 10
LEADERBOARD_TIMEOUT_SECONDS = 120


class LeaderboardView(discord.ui.View):
    PAGE_SIZE = LEADERBOARD_PAGE_SIZE

    def __init__(
        self,
        bot: discord.Client,
        ranked_rows: list[dict],
        caller_id: int,
        *,
        page_size: int = LEADERBOARD_PAGE_SIZE,
        **embed_kwargs,
    ) -> None:
        super().__init__(timeout=LEADERBOARD_TIMEOUT_SECONDS)
        self._bot = bot
        self._ranked_rows = ranked_rows
        self._caller_id = caller_id
        self._page_size = page_size
        self._embed_kwargs = embed_kwargs
        self._current_page = 0
        # Set by the cog after sending so on_timeout can gray out the buttons.
        self.message: discord.Message | None = None

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self._ranked_rows) / self._page_size))

    def build_embed(self) -> discord.Embed:
        return build_leaderboard_embed(
            self._bot,
            self._ranked_rows,
            self._caller_id,
            page=self._current_page,
            page_size=self._page_size,
            **self._embed_kwargs,
        )

    def rebuild_buttons(self) -> None:
        self.clear_items()

        previous_button = discord.ui.Button(
            label='Previous',
            style=discord.ButtonStyle.secondary,
            disabled=(self._current_page == 0),
            row=0,
        )
        previous_button.callback = self._on_previous_page_pressed

        page_label_button = discord.ui.Button(
            label=f'Page {self._current_page + 1} / {self.total_pages}',
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0,
        )

        next_button = discord.ui.Button(
            label='Next',
            style=discord.ButtonStyle.secondary,
            disabled=(self._current_page >= self.total_pages - 1),
            row=0,
        )
        next_button.callback = self._on_next_page_pressed

        self.add_item(previous_button)
        self.add_item(page_label_button)
        self.add_item(next_button)

    def _page_user_ids(self) -> list[int]:
        start = self._current_page * self._page_size
        page_rows = self._ranked_rows[start:start + self._page_size]
        return [row['user_id'] for row in page_rows]

    async def _on_previous_page_pressed(self, interaction: discord.Interaction) -> None:
        self._current_page -= 1
        await self._render_current_page(interaction)

    async def _on_next_page_pressed(self, interaction: discord.Interaction) -> None:
        self._current_page += 1
        await self._render_current_page(interaction)

    async def _render_current_page(self, interaction: discord.Interaction) -> None:
        # Backfill names for players only shown once the user navigates to their page.
        await ensure_display_names(self._bot, self._page_user_ids())
        self.rebuild_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException as error:
            log.warning('Failed to disable leaderboard buttons on timeout: %s', error)
