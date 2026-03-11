"""UI views and modals for deck management commands and game-start deck selection."""

from __future__ import annotations
import random
from typing import TYPE_CHECKING
import discord
from zutomayo.data.deck_storage import (
    add_deck,
    delete_deck,
    get_deck_by_name,
    get_deck_names,
    resolve_deck_cards,
    update_deck,
)
from zutomayo.data.deck_validator import parse_deck_input
from zutomayo.ui.embeds import build_deck_list_embed, create_deck_grid_image

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card


DECKS_PER_PAGE = 25


# ---------------------------------------------------------------------------
# /zutomayo makedeck
# ---------------------------------------------------------------------------


class MakeDeckModal(discord.ui.Modal):
    """Modal for entering a new deck's cards."""

    deck_input = discord.ui.TextInput(
        label='Deck List (20 cards)',
        style=discord.TextStyle.long,
        placeholder='01-001 01-002 02-050 ... (Space-separated card IDs)',
        required=True,
        min_length=20 * 6 + 19,
        max_length=4000,
    )

    def __init__(
        self,
        deck_name: str,
        user_id: int,
        card_index: dict[tuple[int, int], Card],
    ):
        super().__init__(title=f'Create Deck: {deck_name[:35]}', timeout=300)
        self.deck_name = deck_name
        self.user_id = user_id
        self.card_index = card_index

    async def on_submit(self, interaction: discord.Interaction):
        cards, errors = parse_deck_input(self.deck_input.value, self.card_index)

        if errors:
            error_text = '\n'.join(errors)
            if len(error_text) > 1900:
                error_text = error_text[:1900] + '\n... (truncated)'
            await interaction.response.send_message(
                f'**Deck validation failed:**\n```\n{error_text}\n```',
                ephemeral=True,
            )
            return

        try:
            add_deck(self.user_id, self.deck_name, cards)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        embed = build_deck_list_embed(f'Deck Saved: {self.deck_name}', cards)
        await interaction.response.send_message(
            f'Deck **{self.deck_name}** saved successfully!',
            embed=embed,
            ephemeral=True,
        )
        grid = create_deck_grid_image(cards)
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)


# ---------------------------------------------------------------------------
# /zutomayo managedecks
# ---------------------------------------------------------------------------


class EditDeckModal(discord.ui.Modal):
    """Modal for re-entering cards of an existing deck."""

    deck_input = discord.ui.TextInput(
        label='Deck List (20 cards)',
        style=discord.TextStyle.long,
        placeholder='01-001 01-002 02-050 ... (Space-separated card IDs)',
        required=True,
        min_length=20 * 6 + 19,
        max_length=4000,
    )

    def __init__(
        self,
        deck_name: str,
        user_id: int,
        card_index: dict[tuple[int, int], Card],
    ):
        super().__init__(title=f'Edit Deck: {deck_name[:38]}', timeout=300)
        self.deck_name = deck_name
        self.user_id = user_id
        self.card_index = card_index

    async def on_submit(self, interaction: discord.Interaction):
        cards, errors = parse_deck_input(self.deck_input.value, self.card_index)

        if errors:
            error_text = '\n'.join(errors)
            if len(error_text) > 1900:
                error_text = error_text[:1900] + '\n... (truncated)'
            await interaction.response.send_message(
                f'**Deck validation failed:**\n```\n{error_text}\n```',
                ephemeral=True,
            )
            return

        try:
            update_deck(self.user_id, self.deck_name, cards)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        embed = build_deck_list_embed(f'Deck Updated: {self.deck_name}', cards)
        await interaction.response.send_message(
            f'Deck **{self.deck_name}** updated successfully!',
            embed=embed,
            ephemeral=True,
        )
        grid = create_deck_grid_image(cards)
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)


class ManageDecksView(discord.ui.View):
    """Paginated dropdown to select a deck, then Edit or Delete."""

    def __init__(
        self,
        user_id: int,
        deck_names: list[str],
        card_index: dict[tuple[int, int], Card],
        page: int = 0,
    ):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.all_deck_names = deck_names
        self.card_index = card_index
        self.page = page
        self.selected_deck_name: str | None = None
        self._build_page()

    @property
    def total_pages(self) -> int:
        return max(1, -(-len(self.all_deck_names) // DECKS_PER_PAGE))

    def _page_slice(self) -> list[str]:
        start = self.page * DECKS_PER_PAGE
        return self.all_deck_names[start : start + DECKS_PER_PAGE]

    def _build_page(self) -> None:
        names = self._page_slice()
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in names
        ]
        select = discord.ui.Select(
            placeholder='Select a deck to manage...',
            options=options,
        )
        select.callback = self._deck_selected
        self.add_item(select)

        if self.total_pages > 1:
            prev_btn = discord.ui.Button(
                label='<< Prev', style=discord.ButtonStyle.grey,
                disabled=(self.page == 0), row=1,
            )
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label='Next >>', style=discord.ButtonStyle.grey,
                disabled=(self.page >= self.total_pages - 1), row=1,
            )
            next_btn.callback = self._next_page
            self.add_item(next_btn)

    async def _deck_selected(self, interaction: discord.Interaction):
        self.selected_deck_name = interaction.data['values'][0]
        self.clear_items()

        edit_btn = discord.ui.Button(label='Edit', style=discord.ButtonStyle.primary)
        edit_btn.callback = self._edit_deck
        self.add_item(edit_btn)

        delete_btn = discord.ui.Button(label='Delete', style=discord.ButtonStyle.danger)
        delete_btn.callback = self._delete_deck
        self.add_item(delete_btn)

        back_btn = discord.ui.Button(label='Go Back', style=discord.ButtonStyle.grey)
        back_btn.callback = self._go_back
        self.add_item(back_btn)

        await interaction.response.edit_message(
            content=f'Selected deck: **{self.selected_deck_name}**\nChoose an action:',
            view=self,
        )

    async def _edit_deck(self, interaction: discord.Interaction):
        modal = EditDeckModal(self.selected_deck_name, self.user_id, self.card_index)
        await interaction.response.send_modal(modal)

    async def _delete_deck(self, interaction: discord.Interaction):
        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm Delete', style=discord.ButtonStyle.danger)
        confirm_btn.callback = self._confirm_delete
        self.add_item(confirm_btn)

        cancel_btn = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.grey)
        cancel_btn.callback = self._go_back
        self.add_item(cancel_btn)

        await interaction.response.edit_message(
            content=f'Are you sure you want to delete **{self.selected_deck_name}**?',
            view=self,
        )

    async def _confirm_delete(self, interaction: discord.Interaction):
        try:
            delete_deck(self.user_id, self.selected_deck_name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.all_deck_names = get_deck_names(self.user_id)
        if not self.all_deck_names:
            await interaction.response.edit_message(
                content=f'Deck **{self.selected_deck_name}** deleted. You have no more saved decks.',
                view=None,
            )
            self.stop()
            return

        if self.page >= self.total_pages:
            self.page = self.total_pages - 1

        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Deck **{self.selected_deck_name}** deleted. Select another deck to manage:',
            view=self,
        )

    async def _go_back(self, interaction: discord.Interaction):
        self.selected_deck_name = None
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content='Select a deck to manage:',
            view=self,
        )

    async def _prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Select a deck to manage (Page {self.page + 1}/{self.total_pages}):',
            view=self,
        )

    async def _next_page(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Select a deck to manage (Page {self.page + 1}/{self.total_pages}):',
            view=self,
        )


# ---------------------------------------------------------------------------
# /zutomayo viewdeck
# ---------------------------------------------------------------------------


class ViewDeckView(discord.ui.View):
    """Paginated deck viewer. Shows one deck at a time with Prev/Next buttons."""

    def __init__(
        self,
        user_id: int,
        decks: list[dict],
        card_index: dict[tuple[int, int], Card],
        page: int = 0,
    ):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.decks = decks
        self.card_index = card_index
        self.page = page
        self._build_buttons()

    def _build_buttons(self) -> None:
        if len(self.decks) > 1:
            prev_btn = discord.ui.Button(
                label='<< Prev Deck', style=discord.ButtonStyle.grey,
                disabled=(self.page == 0),
            )
            prev_btn.callback = self._prev_deck
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label='Next Deck >>', style=discord.ButtonStyle.grey,
                disabled=(self.page >= len(self.decks) - 1),
            )
            next_btn.callback = self._next_deck
            self.add_item(next_btn)

    def current_cards(self) -> list:
        """Return the resolved cards for the currently displayed deck."""
        deck = self.decks[self.page]
        return resolve_deck_cards(deck, self.card_index)

    def current_embed(self) -> discord.Embed:
        deck = self.decks[self.page]
        cards = resolve_deck_cards(deck, self.card_index)
        title = f'{deck["name"]} ({self.page + 1}/{len(self.decks)})'
        embed = build_deck_list_embed(title, cards)
        ids_line = ' '.join(f'{c.pack:02d}-{c.id:03d}' for c in cards)
        embed.description += f'\n\n{ids_line}'
        return embed

    async def _prev_deck(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.clear_items()
        self._build_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)
        grid = create_deck_grid_image(self.current_cards())
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)

    async def _next_deck(self, interaction: discord.Interaction):
        self.page = min(len(self.decks) - 1, self.page + 1)
        self.clear_items()
        self._build_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)
        grid = create_deck_grid_image(self.current_cards())
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)


# ---------------------------------------------------------------------------
# Game start: deck source choice
# ---------------------------------------------------------------------------


def _random_deck(all_cards: list[Card]) -> list[Card]:
    """Generate a random 20-card deck (max 2 copies per card)."""
    pool = all_cards * 2
    random.shuffle(pool)
    deck: list[Card] = []
    counts: dict[tuple[int, int], int] = {}
    for card in pool:
        key = (card.pack, card.id)
        if counts.get(key, 0) < 2 and len(deck) < 20:
            deck.append(card)
            counts[key] = counts.get(key, 0) + 1
    return deck


class DeckSourceView(discord.ui.View):
    """Pre-deck-building choice: build from scratch or pick a saved deck."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        all_cards: list[Card],
        card_index: dict[tuple[int, int], Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=600)
        self.session = session
        self.player_index = player_index
        self.all_cards = all_cards
        self.card_index = card_index
        self.opponent_name = opponent_name

    @discord.ui.button(label='Build a Deck', style=discord.ButtonStyle.primary, row=0)
    async def build_deck(self, interaction: discord.Interaction, button: discord.ui.Button):
        from zutomayo.ui.deck_builder_view import DeckBuilderView

        view = DeckBuilderView(self.session, self.player_index, self.all_cards, self.opponent_name)
        await interaction.response.edit_message(
            content=(
                '**Deck Building [デッキ構築]**\n'
                'Build your 20-card deck!\n'
                'Click **Enter Deck** to type your deck list as space-separated '
                'card IDs in `XX-YYY` format (e.g. `01-001 01-002 02-050 ...`).\n'
                'Max 2 copies of any card. Click **Random Deck** for a quick start.'
            ),
            view=view,
        )
        self.stop()

    @discord.ui.button(label='Select a Deck', style=discord.ButtonStyle.secondary, row=0)
    async def select_deck(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        deck_names = get_deck_names(user_id)
        if not deck_names:
            await interaction.response.send_message(
                'You have no saved decks. Use `/zutomayo makedeck` to create one, or click **Build a Deck**.',
                ephemeral=True,
            )
            return

        view = SavedDeckSelectView(
            session=self.session,
            player_index=self.player_index,
            user_id=user_id,
            deck_names=deck_names,
            card_index=self.card_index,
            all_cards=self.all_cards,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='Select a saved deck:',
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, _random_deck(self.all_cards))


class SavedDeckSelectView(discord.ui.View):
    """Paginated dropdown of saved decks during game start."""

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
        super().__init__(timeout=600)
        self.session = session
        self.player_index = player_index
        self.user_id = user_id
        self.all_deck_names = deck_names
        self.card_index = card_index
        self.all_cards = all_cards
        self.opponent_name = opponent_name
        self.page = page
        self._build_page()

    @property
    def total_pages(self) -> int:
        return max(1, -(-len(self.all_deck_names) // DECKS_PER_PAGE))

    def _page_slice(self) -> list[str]:
        start = self.page * DECKS_PER_PAGE
        return self.all_deck_names[start : start + DECKS_PER_PAGE]

    def _build_page(self) -> None:
        names = self._page_slice()
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in names
        ]
        select = discord.ui.Select(placeholder='Select a deck...', options=options)
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
        deck_data = get_deck_by_name(self.user_id, deck_name)
        if deck_data is None:
            await interaction.response.send_message('Deck not found.', ephemeral=True)
            return

        cards = resolve_deck_cards(deck_data, self.card_index)
        embed = build_deck_list_embed(f'Deck: {deck_name}', cards)

        view = SavedDeckConfirmView(
            session=self.session,
            player_index=self.player_index,
            user_id=self.user_id,
            deck_name=deck_name,
            cards=cards,
            card_index=self.card_index,
            all_cards=self.all_cards,
            all_deck_names=self.all_deck_names,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content=f'You selected **{deck_name}**:',
            embed=embed,
            view=view,
        )
        self.stop()
        grid = create_deck_grid_image(cards)
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)

    async def _prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Select a deck (Page {self.page + 1}/{self.total_pages}):',
            view=self,
        )

    async def _next_page(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.clear_items()
        self._build_page()
        await interaction.response.edit_message(
            content=f'Select a deck (Page {self.page + 1}/{self.total_pages}):',
            view=self,
        )

    async def _go_back(self, interaction: discord.Interaction):
        view = DeckSourceView(
            self.session, self.player_index, self.all_cards,
            self.card_index, self.opponent_name,
        )
        await interaction.response.edit_message(
            content=(
                '**Deck Building [デッキ構築]**\n'
                'Choose how to build your deck:\n'
                '**Build a Deck** - Enter cards manually or get a random deck\n'
                '**Select a Deck** - Use one of your saved decks'
            ),
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, _random_deck(self.all_cards))


class SavedDeckConfirmView(discord.ui.View):
    """Confirm or Go Back after viewing a saved deck during game start."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        user_id: int,
        deck_name: str,
        cards: list[Card],
        card_index: dict[tuple[int, int], Card],
        all_cards: list[Card],
        all_deck_names: list[str],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=600)
        self.session = session
        self.player_index = player_index
        self.user_id = user_id
        self.deck_name = deck_name
        self.cards = cards
        self.card_index = card_index
        self.all_cards = all_cards
        self.all_deck_names = all_deck_names
        self.opponent_name = opponent_name

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.session.submit_action(self.player_index, self.cards)
        await interaction.response.edit_message(
            content=f'Deck **{self.deck_name}** confirmed! Waiting for {self.opponent_name}...',
            embed=None,
            view=None,
        )
        self.stop()

    @discord.ui.button(label='Go Back', style=discord.ButtonStyle.grey)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SavedDeckSelectView(
            session=self.session,
            player_index=self.player_index,
            user_id=self.user_id,
            deck_names=self.all_deck_names,
            card_index=self.card_index,
            all_cards=self.all_cards,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='Select a saved deck:',
            embed=None,
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, _random_deck(self.all_cards))
