"""Confirmation view for resuming a saved PvP game: both players must agree
before replay starts. The invoker asked via /zutomayo resume; the opponent
accepts or declines here. On timeout or decline the game simply stays saved."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import discord

log = logging.getLogger(__name__)

RESUME_CONFIRMATION_TIMEOUT_SECONDS = 300


class ResumeConfirmationView(discord.ui.View):
    def __init__(
        self,
        game_id: str,
        invoker_id: int,
        opponent_id: int,
        on_accept: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=RESUME_CONFIRMATION_TIMEOUT_SECONDS)
        self.game_id = game_id
        self.invoker_id = invoker_id
        self.opponent_id = opponent_id
        self.on_accept = on_accept
        self.message: Optional[discord.Message] = None
        self.resolved = False

    async def _finish(self, interaction: discord.Interaction, content: str) -> None:
        self.resolved = True
        self.stop()
        await interaction.response.edit_message(content=content, view=None)

    @discord.ui.button(label='Accept and Resume', style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                'Only your opponent can accept this resume request.', ephemeral=True,
            )
            return
        self.resolved = True
        self.stop()
        await self.on_accept(interaction)

    @discord.ui.button(label='Decline', style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                'Only your opponent can decline this resume request.', ephemeral=True,
            )
            return
        await self._finish(
            interaction,
            f'Resume request for game `{self.game_id}` was declined. The game remains saved.',
        )

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                'Only the player who requested the resume can cancel it.', ephemeral=True,
            )
            return
        await self._finish(
            interaction,
            f'Resume request for game `{self.game_id}` was cancelled. The game remains saved.',
        )

    async def on_timeout(self) -> None:
        if self.resolved or self.message is None:
            return
        try:
            await self.message.edit(
                content=(
                    f'Resume request for game `{self.game_id}` expired. '
                    'The game remains saved.'
                ),
                view=None,
            )
        except discord.HTTPException:
            log.warning('Failed to edit expired resume confirmation for game %s', self.game_id)
