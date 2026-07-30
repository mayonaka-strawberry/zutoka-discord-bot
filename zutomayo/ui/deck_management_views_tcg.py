"""UI views and modals for TCG deck management commands and game-start TCG deck selection."""

from __future__ import annotations
import random
from typing import TYPE_CHECKING
import discord
from zutomayo.ui.deck_management_common import DECKS_PER_PAGE, DeckNamePaginationMixin
from zutomayo.data.deck_storage_tcg import (
    add_tcg_deck,
    delete_tcg_deck,
    get_tcg_deck_by_name,
    get_tcg_deck_names,
    resolve_tcg_deck_cards,
    update_tcg_deck,
)
from zutomayo.data.deck_validator_tcg import parse_tcg_deck_input
from zutomayo.ui.embeds import build_deck_list_embed, create_deck_grid_image_off_thread
from zutomayo.utils.discord_utils import send_images_with_retry

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card




# ---------------------------------------------------------------------------
# /zutomayo makedecktcg
# ---------------------------------------------------------------------------


class MakeDeckTcgModal(discord.ui.Modal):
    """Modal for entering a new TCG deck's main and side deck cards."""

    deck_input = discord.ui.TextInput(
        label='Main Deck (20 cards)',
        style=discord.TextStyle.long,
        placeholder='01-001 01-002 02-050 ... (Space-separated card IDs)',
        required=True,
        min_length=20 * 6 + 19,
        max_length=4000,
    )

    side_deck_input = discord.ui.TextInput(
        label='Side Deck (8 cards)',
        style=discord.TextStyle.long,
        placeholder='03-001 03-002 ... (Space-separated card IDs)',
        required=True,
        min_length=8 * 6 + 7,
        max_length=4000,
    )

    def __init__(
        self,
        deck_name: str,
        user_id: int,
        card_index: dict[tuple[int, int], Card],
    ):
        super().__init__(title=f'Create TCG Deck: {deck_name[:30]}', timeout=750)
        self.deck_name = deck_name
        self.user_id = user_id
        self.card_index = card_index

    async def on_submit(self, interaction: discord.Interaction):
        result, errors = parse_tcg_deck_input(
            self.deck_input.value, self.side_deck_input.value, self.card_index,
        )

        if errors:
            error_text = '\n'.join(errors)
            if len(error_text) > 1900:
                error_text = error_text[:1900] + '\n... (truncated)'
            await interaction.response.send_message(
                f'**Deck validation failed:**\n```\n{error_text}\n```',
                ephemeral=True,
            )
            return

        main_cards, side_cards = result
        try:
            await add_tcg_deck(self.user_id, self.deck_name, main_cards, side_cards)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        embed = build_deck_list_embed(f'TCG Deck Saved: {self.deck_name}', main_cards)
        side_embed = build_deck_list_embed('Side Deck', side_cards)
        await interaction.response.send_message(
            f'TCG Deck **{self.deck_name}** saved successfully!',
            embeds=[embed, side_embed],
            ephemeral=True,
        )
        grid = await create_deck_grid_image_off_thread(main_cards)
        if grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Main Deck:**',
                file=grid,
                ephemeral=True,
            )
        side_grid = await create_deck_grid_image_off_thread(side_cards, columns=4)
        if side_grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Side Deck:**',
                file=side_grid,
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# /zutomayo managedeckstcg
# ---------------------------------------------------------------------------


class EditDeckTcgModal(discord.ui.Modal):
    """Modal for re-entering cards of an existing TCG deck."""

    deck_input = discord.ui.TextInput(
        label='Main Deck (20 cards)',
        style=discord.TextStyle.long,
        placeholder='01-001 01-002 02-050 ... (Space-separated card IDs)',
        required=True,
        min_length=20 * 6 + 19,
        max_length=4000,
    )

    side_deck_input = discord.ui.TextInput(
        label='Side Deck (8 cards)',
        style=discord.TextStyle.long,
        placeholder='03-001 03-002 ... (Space-separated card IDs)',
        required=True,
        min_length=8 * 6 + 7,
        max_length=4000,
    )

    def __init__(
        self,
        deck_name: str,
        user_id: int,
        card_index: dict[tuple[int, int], Card],
        current_main_cards: list[Card] | None = None,
        current_side_cards: list[Card] | None = None,
    ):
        super().__init__(title=f'Edit TCG Deck: {deck_name[:33]}', timeout=750)
        self.deck_name = deck_name
        self.user_id = user_id
        self.card_index = card_index
        from zutomayo.ui.deck_management_views import format_card_ids_line

        if current_main_cards:
            self.deck_input.default = format_card_ids_line(current_main_cards)
        if current_side_cards:
            self.side_deck_input.default = format_card_ids_line(current_side_cards)

    async def on_submit(self, interaction: discord.Interaction):
        result, errors = parse_tcg_deck_input(
            self.deck_input.value, self.side_deck_input.value, self.card_index,
        )

        if errors:
            error_text = '\n'.join(errors)
            if len(error_text) > 1900:
                error_text = error_text[:1900] + '\n... (truncated)'
            await interaction.response.send_message(
                f'**Deck validation failed:**\n```\n{error_text}\n```',
                ephemeral=True,
            )
            return

        main_cards, side_cards = result
        try:
            await update_tcg_deck(self.user_id, self.deck_name, main_cards, side_cards)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        embed = build_deck_list_embed(f'TCG Deck Updated: {self.deck_name}', main_cards)
        side_embed = build_deck_list_embed('Side Deck', side_cards)
        await interaction.response.send_message(
            f'TCG Deck **{self.deck_name}** updated successfully!',
            embeds=[embed, side_embed],
            ephemeral=True,
        )
        grid = await create_deck_grid_image_off_thread(main_cards)
        if grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Main Deck:**',
                file=grid,
                ephemeral=True,
            )
        side_grid = await create_deck_grid_image_off_thread(side_cards, columns=4)
        if side_grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Side Deck:**',
                file=side_grid,
                ephemeral=True,
            )


class ManageDeckTcgActionsView(discord.ui.View):
    """Edit / Delete actions for one TCG deck chosen through command autocomplete."""

    def __init__(
        self,
        user_id: int,
        deck_name: str,
        card_index: dict[tuple[int, int], Card],
    ):
        super().__init__(timeout=750)
        self.user_id = user_id
        self.deck_name = deck_name
        self.card_index = card_index
        self._build_action_buttons()

    def _build_action_buttons(self) -> None:
        self.clear_items()

        edit_button = discord.ui.Button(label='Edit', style=discord.ButtonStyle.primary)
        edit_button.callback = self._edit_deck
        self.add_item(edit_button)

        delete_button = discord.ui.Button(label='Delete', style=discord.ButtonStyle.danger)
        delete_button.callback = self._delete_deck
        self.add_item(delete_button)

    async def _edit_deck(self, interaction: discord.Interaction):
        deck_data = await get_tcg_deck_by_name(self.user_id, self.deck_name)
        if deck_data is None:
            await interaction.response.send_message(
                f'TCG deck **{self.deck_name}** no longer exists.', ephemeral=True,
            )
            return
        main_cards, side_cards = resolve_tcg_deck_cards(deck_data, self.card_index)
        modal = EditDeckTcgModal(
            self.deck_name, self.user_id, self.card_index,
            current_main_cards=main_cards, current_side_cards=side_cards,
        )
        await interaction.response.send_modal(modal)

    async def _delete_deck(self, interaction: discord.Interaction):
        self.clear_items()

        confirm_button = discord.ui.Button(label='Confirm Delete', style=discord.ButtonStyle.danger)
        confirm_button.callback = self._confirm_delete
        self.add_item(confirm_button)

        cancel_button = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.grey)
        cancel_button.callback = self._cancel_delete
        self.add_item(cancel_button)

        await interaction.response.edit_message(
            content=f'Are you sure you want to delete **{self.deck_name}**?',
            view=self,
        )

    async def _confirm_delete(self, interaction: discord.Interaction):
        try:
            await delete_tcg_deck(self.user_id, self.deck_name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.edit_message(
            content=f'TCG deck **{self.deck_name}** deleted.',
            embed=None,
            view=None,
        )
        self.stop()

    async def _cancel_delete(self, interaction: discord.Interaction):
        self._build_action_buttons()
        await interaction.response.edit_message(
            content='Choose an action:',
            view=self,
        )


# ---------------------------------------------------------------------------
# Game start: TCG deck source choice
# ---------------------------------------------------------------------------


def _random_tcg_deck(all_cards: list[Card]) -> tuple[list[Card], list[Card]]:
    """Generate a random TCG deck: 20 main + 8 side (max 2 copies across both)."""
    pool = all_cards * 2
    random.shuffle(pool)
    main: list[Card] = []
    side: list[Card] = []
    counts: dict[tuple[int, int], int] = {}
    for card in pool:
        key = (card.pack, card.id)
        if counts.get(key, 0) < 2:
            if len(main) < 20:
                main.append(card)
                counts[key] = counts.get(key, 0) + 1
            elif len(side) < 8:
                side.append(card)
                counts[key] = counts.get(key, 0) + 1
    return main, side


class TcgDeckInputModal(discord.ui.Modal):
    """Modal for entering a TCG deck list during game start."""

    deck_input = discord.ui.TextInput(
        label='Main Deck (20 cards)',
        style=discord.TextStyle.long,
        placeholder='01-001 01-002 02-050 ... (Space-separated card IDs)',
        required=True,
        min_length=20 * 6 + 19,
        max_length=4000,
    )

    side_deck_input = discord.ui.TextInput(
        label='Side Deck (8 cards)',
        style=discord.TextStyle.long,
        placeholder='03-001 03-002 ... (Space-separated card IDs)',
        required=True,
        min_length=8 * 6 + 7,
        max_length=4000,
    )

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        card_index: dict[tuple[int, int], Card],
        parent_view: TcgDeckBuilderView,
    ):
        super().__init__(title='TCG Deck Building [デッキ構築]', timeout=750)
        self.session = session
        self.player_index = player_index
        self.card_index = card_index
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        result, errors = parse_tcg_deck_input(
            self.deck_input.value, self.side_deck_input.value, self.card_index,
        )

        if errors:
            error_text = '\n'.join(errors)
            if len(error_text) > 1900:
                error_text = error_text[:1900] + '\n... (truncated)'
            await interaction.response.send_message(
                f'**Deck validation failed:**\n```\n{error_text}\n```\n'
                'Click **Enter Deck** to try again.',
                ephemeral=True,
            )
            return

        main_cards, side_cards = result
        self.session.player_deck_names[self.player_index] = '<manual>'
        self.session.submit_action(self.player_index, {'deck': main_cards, 'side_deck': side_cards})
        await interaction.response.edit_message(
            content=f'Waiting for {self.parent_view.opponent_name}...',
            view=None,
        )
        self.parent_view.stop()


class TcgDeckBuilderView(discord.ui.View):
    """TCG deck builder with two options: enter a deck list or get a random deck."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        all_cards: list[Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.all_cards = all_cards
        from zutomayo.data.deck_validator import build_card_index
        self.card_index = build_card_index(all_cards)
        self.opponent_name = opponent_name

    @discord.ui.button(label='Enter Deck', style=discord.ButtonStyle.primary, row=0)
    async def enter_deck_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TcgDeckInputModal(
            session=self.session,
            player_index=self.player_index,
            card_index=self.card_index,
            parent_view=self,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Random Deck', style=discord.ButtonStyle.secondary, row=0)
    async def random_deck_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        main, side = _random_tcg_deck(self.all_cards)
        self.session.player_deck_names[self.player_index] = '<random>'
        self.session.submit_action(self.player_index, {'deck': main, 'side_deck': side})
        await interaction.response.edit_message(
            content=f'Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            main, side = _random_tcg_deck(self.all_cards)
            self.session.player_deck_names[self.player_index] = '<random>'
            self.session.submit_action(self.player_index, {'deck': main, 'side_deck': side})


class TcgDeckSourceView(discord.ui.View):
    """Pre-deck-building choice for TCG: build from scratch or pick a saved TCG deck."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        all_cards: list[Card],
        card_index: dict[tuple[int, int], Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.all_cards = all_cards
        self.card_index = card_index
        self.opponent_name = opponent_name

    @discord.ui.button(label='Build a Deck', style=discord.ButtonStyle.primary, row=0)
    async def build_deck(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TcgDeckBuilderView(self.session, self.player_index, self.all_cards, self.opponent_name)
        await interaction.response.edit_message(
            content=(
                '**TCG Deck Building [デッキ構築]**\n'
                'Build your 20-card main deck and 8-card side deck!\n'
                'Click **Enter Deck** to type your deck lists as space-separated '
                'card IDs in `XX-YYY` format.\n'
                'Max 2 copies of any card across both decks. Click **Random Deck** for a quick start.'
            ),
            view=view,
        )
        self.stop()

    @discord.ui.button(label='Select a Deck', style=discord.ButtonStyle.secondary, row=0)
    async def select_deck(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        deck_names = await get_tcg_deck_names(user_id)
        if not deck_names:
            await interaction.response.send_message(
                'You have no saved TCG decks. Use `/zutomayo makedecktcg` to create one, or click **Build a Deck**.',
                ephemeral=True,
            )
            return

        view = TcgSavedDeckSelectView(
            session=self.session,
            player_index=self.player_index,
            user_id=user_id,
            deck_names=deck_names,
            card_index=self.card_index,
            all_cards=self.all_cards,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='Select a saved TCG deck:',
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            main, side = _random_tcg_deck(self.all_cards)
            self.session.player_deck_names[self.player_index] = '<random>'
            self.session.submit_action(self.player_index, {'deck': main, 'side_deck': side})


class TcgSavedDeckSelectView(DeckNamePaginationMixin, discord.ui.View):
    """Paginated dropdown of saved TCG decks during game start."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        user_id: int,
        deck_names: list[str],
        card_index: dict[tuple[int, int], Card],
        all_cards: list[Card],
        opponent_name: str = 'opponent',
        page: int = 0,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.user_id = user_id
        self.all_deck_names = deck_names
        self.card_index = card_index
        self.all_cards = all_cards
        self.opponent_name = opponent_name
        self.page = page
        self._build_page()

    def _build_page(self) -> None:
        names = self._page_slice()
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in names
        ]
        select = discord.ui.Select(placeholder='Select a TCG deck...', options=options)
        select.callback = self._deck_selected
        self.add_item(select)

        if self.total_pages > 1:
            prev_btn = discord.ui.Button(
                label='<< Prev', style=discord.ButtonStyle.grey,
                row=1, disabled=(self.page == 0),
            )
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label='Next >>', style=discord.ButtonStyle.grey,
                row=1, disabled=(self.page >= self.total_pages - 1),
            )
            next_btn.callback = self._next_page
            self.add_item(next_btn)

        back_btn = discord.ui.Button(label='Go Back', style=discord.ButtonStyle.grey, row=2)
        back_btn.callback = self._go_back
        self.add_item(back_btn)

    async def _deck_selected(self, interaction: discord.Interaction):
        deck_name = interaction.data['values'][0]
        deck_data = await get_tcg_deck_by_name(self.user_id, deck_name)
        if deck_data is None:
            await interaction.response.send_message('Deck not found.', ephemeral=True)
            return

        main_cards, side_cards = resolve_tcg_deck_cards(deck_data, self.card_index)
        main_embed = build_deck_list_embed(f'TCG Deck: {deck_name} - Main', main_cards)
        side_embed = build_deck_list_embed(f'TCG Deck: {deck_name} - Side', side_cards)

        view = TcgSavedDeckConfirmView(
            session=self.session,
            player_index=self.player_index,
            user_id=self.user_id,
            deck_name=deck_name,
            main_cards=main_cards,
            side_cards=side_cards,
            card_index=self.card_index,
            all_cards=self.all_cards,
            all_deck_names=self.all_deck_names,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content=f'You selected **{deck_name}**:',
            embeds=[main_embed, side_embed],
            view=view,
        )
        self.stop()
        grid = await create_deck_grid_image_off_thread(main_cards)
        if grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Main Deck:**',
                file=grid,
                ephemeral=True,
            )
        side_grid = await create_deck_grid_image_off_thread(side_cards, columns=4)
        if side_grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Side Deck:**',
                file=side_grid,
                ephemeral=True,
            )

    async def _prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Select a TCG deck (Page {self.page + 1}/{self.total_pages}):',
            view=self,
        )

    async def _next_page(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Select a TCG deck (Page {self.page + 1}/{self.total_pages}):',
            view=self,
        )

    async def _go_back(self, interaction: discord.Interaction):
        view = TcgDeckSourceView(
            self.session, self.player_index, self.all_cards,
            self.card_index, self.opponent_name,
        )
        await interaction.response.edit_message(
            content=(
                '**TCG Deck Building [デッキ構築]**\n'
                'Choose how to build your deck:\n'
                '**Build a Deck** - Enter cards manually or get a random deck\n'
                '**Select a Deck** - Use one of your saved TCG decks'
            ),
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            main, side = _random_tcg_deck(self.all_cards)
            self.session.player_deck_names[self.player_index] = '<random>'
            self.session.submit_action(self.player_index, {'deck': main, 'side_deck': side})


class TcgSavedDeckConfirmView(discord.ui.View):
    """Confirm or Go Back after viewing a saved TCG deck during game start."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        user_id: int,
        deck_name: str,
        main_cards: list[Card],
        side_cards: list[Card],
        card_index: dict[tuple[int, int], Card],
        all_cards: list[Card],
        all_deck_names: list[str],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.user_id = user_id
        self.deck_name = deck_name
        self.main_cards = main_cards
        self.side_cards = side_cards
        self.card_index = card_index
        self.all_cards = all_cards
        self.all_deck_names = all_deck_names
        self.opponent_name = opponent_name

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.session.player_deck_names[self.player_index] = self.deck_name
        self.session.submit_action(self.player_index, {'deck': self.main_cards, 'side_deck': self.side_cards})
        await interaction.response.edit_message(
            content=f'TCG Deck **{self.deck_name}** confirmed! Waiting for {self.opponent_name}...',
            embeds=[],
            view=None,
        )
        self.stop()

    @discord.ui.button(label='Go Back', style=discord.ButtonStyle.grey)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TcgSavedDeckSelectView(
            session=self.session,
            player_index=self.player_index,
            user_id=self.user_id,
            deck_names=self.all_deck_names,
            card_index=self.card_index,
            all_cards=self.all_cards,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='Select a saved TCG deck:',
            embeds=[],
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            main, side = _random_tcg_deck(self.all_cards)
            self.session.player_deck_names[self.player_index] = '<random>'
            self.session.submit_action(self.player_index, {'deck': main, 'side_deck': side})
