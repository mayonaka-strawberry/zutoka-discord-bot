"""UI views for the card switching phase between TCG matches."""

from __future__ import annotations
from typing import TYPE_CHECKING
import discord
from zutomayo.enums.card_type import CardType
from zutomayo.ui.embeds import ATTRIBUTE_EN, ATTRIBUTE_JP, CARD_TYPE_LABEL

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card


def _card_option(card: Card, index: int) -> discord.SelectOption:
    """Build a select option for a Card object."""
    attr_en = ATTRIBUTE_EN.get(card.attribute.value, card.attribute.value)
    attr_jp = ATTRIBUTE_JP.get(card.attribute.value, '')
    type_label = CARD_TYPE_LABEL.get(card.card_type, '')
    rarity = card.rarity.value
    label = f'{card.pack:02d}-{card.id:03d} {card.name} [{card.name_jp}]'
    if len(label) > 100:
        label = label[:97] + '...'
    if card.card_type == CardType.CHARACTER:
        description = (
            f'{rarity} | {type_label} | {attr_en} [{attr_jp}] | CLK: {card.clock}'
            f' | N: {card.attack_night} | D: {card.attack_day}'
            f' | Cost: {card.power_cost} | STP: {card.send_to_power}'
        )
    else:
        description = (
            f'{rarity} | {type_label} | {attr_en} [{attr_jp}] | CLK: {card.clock}'
            f' | Cost: {card.power_cost} | STP: {card.send_to_power}'
        )
    if len(description) > 100:
        description = description[:97] + '...'
    return discord.SelectOption(label=label, description=description, value=str(index))


class SwitchCardsView(discord.ui.View):
    """Initial view: choose to switch cards or keep current deck."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        main_deck: list[Card],
        side_deck: list[Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.main_deck = main_deck
        self.side_deck = side_deck
        self.opponent_name = opponent_name

    @discord.ui.button(label='Switch Cards', style=discord.ButtonStyle.primary, row=0)
    async def switch_cards(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RemoveCardsView(
            session=self.session,
            player_index=self.player_index,
            main_deck=self.main_deck,
            side_deck=self.side_deck,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='**Select cards to remove from your main deck** (1-8 cards):',
            view=view,
        )
        self.stop()

    @discord.ui.button(label='No Changes', style=discord.ButtonStyle.secondary, row=0)
    async def no_changes(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = NoChangeConfirmView(
            session=self.session,
            player_index=self.player_index,
            main_deck=self.main_deck,
            side_deck=self.side_deck,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='**No Changes** — Are you sure?',
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, {'removed': [], 'added': []})


class NoChangeConfirmView(discord.ui.View):
    """Confirm or go back when choosing no changes."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        main_deck: list[Card],
        side_deck: list[Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.main_deck = main_deck
        self.side_deck = side_deck
        self.opponent_name = opponent_name

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.session.submit_action(self.player_index, {'removed': [], 'added': []})
        await interaction.response.edit_message(
            content=f'No changes made. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    @discord.ui.button(label='Go Back', style=discord.ButtonStyle.grey)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SwitchCardsView(
            session=self.session,
            player_index=self.player_index,
            main_deck=self.main_deck,
            side_deck=self.side_deck,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='**Switch Cards [サイドデッキの入れ替え]**\nSwap cards between your main deck and side deck.',
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, {'removed': [], 'added': []})


class RemoveCardsView(discord.ui.View):
    """Select 1-8 cards from main deck to remove."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        main_deck: list[Card],
        side_deck: list[Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.main_deck = main_deck
        self.side_deck = side_deck
        self.opponent_name = opponent_name
        self.selected_remove_indices: list[int] = []
        self._build_select()

    def _build_select(self) -> None:
        options = [_card_option(card, i) for i, card in enumerate(self.main_deck)]
        select = discord.ui.Select(
            placeholder='Select cards to remove (1-8)...',
            options=options,
            min_values=1,
            max_values=min(8, len(options)),
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        self.selected_remove_indices = [int(v) for v in interaction.data['values']]
        # Replace select with confirm/back buttons
        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_btn.callback = self._confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label='Back to Switch', style=discord.ButtonStyle.grey)
        back_btn.callback = self._go_back
        self.add_item(back_btn)

        removed_names = ', '.join(
            f'{self.main_deck[i].pack:02d}-{self.main_deck[i].id:03d}' for i in self.selected_remove_indices
        )
        await interaction.response.edit_message(
            content=f'**Cards to remove ({len(self.selected_remove_indices)}):** {removed_names}\n\nConfirm or go back?',
            view=self,
        )

    async def _confirm(self, interaction: discord.Interaction):
        removed_cards = [self.main_deck[i] for i in self.selected_remove_indices]
        count = len(removed_cards)

        if count == 8:
            # All side deck cards added automatically
            self.session.submit_action(self.player_index, {
                'removed': removed_cards,
                'added': list(self.side_deck),
            })
            await interaction.response.edit_message(
                content=f'Removed {count} cards and added all 8 side deck cards. Waiting for {self.opponent_name}...',
                view=None,
            )
            self.stop()
        else:
            # Show side deck selection
            view = AddCardsView(
                session=self.session,
                player_index=self.player_index,
                main_deck=self.main_deck,
                side_deck=self.side_deck,
                removed_cards=removed_cards,
                removed_indices=self.selected_remove_indices,
                count_needed=count,
                opponent_name=self.opponent_name,
            )
            await interaction.response.edit_message(
                content=f'**Select {count} card(s) from your side deck to add:**',
                view=view,
            )
            self.stop()

    async def _go_back(self, interaction: discord.Interaction):
        view = SwitchCardsView(
            session=self.session,
            player_index=self.player_index,
            main_deck=self.main_deck,
            side_deck=self.side_deck,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='**Switch Cards [サイドデッキの入れ替え]**\nSwap cards between your main deck and side deck.',
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, {'removed': [], 'added': []})


class AddCardsView(discord.ui.View):
    """Select N cards from side deck to add to main deck."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        main_deck: list[Card],
        side_deck: list[Card],
        removed_cards: list[Card],
        removed_indices: list[int],
        count_needed: int,
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.main_deck = main_deck
        self.side_deck = side_deck
        self.removed_cards = removed_cards
        self.removed_indices = removed_indices
        self.count_needed = count_needed
        self.opponent_name = opponent_name
        self.selected_add_indices: list[int] = []
        self._build_select()

    def _build_select(self) -> None:
        options = [_card_option(card, i) for i, card in enumerate(self.side_deck)]
        select = discord.ui.Select(
            placeholder=f'Select {self.count_needed} card(s) to add...',
            options=options,
            min_values=self.count_needed,
            max_values=self.count_needed,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        self.selected_add_indices = [int(v) for v in interaction.data['values']]
        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_btn.callback = self._confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label='Back to Select', style=discord.ButtonStyle.grey)
        back_btn.callback = self._go_back
        self.add_item(back_btn)

        added_names = ', '.join(
            f'{self.side_deck[i].pack:02d}-{self.side_deck[i].id:03d}' for i in self.selected_add_indices
        )
        await interaction.response.edit_message(
            content=f'**Cards to add ({len(self.selected_add_indices)}):** {added_names}\n\nConfirm or go back?',
            view=self,
        )

    async def _confirm(self, interaction: discord.Interaction):
        added_cards = [self.side_deck[i] for i in self.selected_add_indices]
        self.session.submit_action(self.player_index, {
            'removed': self.removed_cards,
            'added': added_cards,
        })
        await interaction.response.edit_message(
            content=f'Switched {len(self.removed_cards)} card(s). Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _go_back(self, interaction: discord.Interaction):
        view = RemoveCardsView(
            session=self.session,
            player_index=self.player_index,
            main_deck=self.main_deck,
            side_deck=self.side_deck,
            opponent_name=self.opponent_name,
        )
        await interaction.response.edit_message(
            content='**Select cards to remove from your main deck** (1-8 cards):',
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            self.session.submit_action(self.player_index, {'removed': [], 'added': []})
