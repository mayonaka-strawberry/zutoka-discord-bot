from __future__ import annotations
import random
from typing import TYPE_CHECKING
import discord
from zutomayo.data.deck_validator import build_card_index, parse_deck_input

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card


class DeckInputModal(discord.ui.Modal):
    """Modal with a text input for entering a deck list as XX-YYY tokens."""

    deck_input = discord.ui.TextInput(
        label='Deck List (20 cards)',
        style=discord.TextStyle.long,
        placeholder='01-001 01-002 02-050 ... (Space-separated card IDs)',
        required=True,
        min_length=20 * 6 + 19,  # 20 tokens of 6 chars + 19 spaces = 139
        max_length=4000,
    )

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        card_index: dict[tuple[int, int], Card],
        parent_view: DeckBuilderView,
    ):
        super().__init__(title='Deck Building [デッキ構築]', timeout=300)
        self.session = session
        self.player_index = player_index
        self.card_index = card_index
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        cards, errors = parse_deck_input(self.deck_input.value, self.card_index)

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

        self.session.submit_action(self.player_index, cards)
        await interaction.response.edit_message(
            content=f'Waiting for {self.parent_view.opponent_name}...',
            view=None,
        )
        self.parent_view.stop()


class DeckBuilderView(discord.ui.View):
    """Simplified deck builder with two options: enter a deck list or get a random deck."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        all_cards: list[Card],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=600)
        self.session = session
        self.player_index = player_index
        self.all_cards = all_cards
        self.card_index = build_card_index(all_cards)
        self.opponent_name = opponent_name

    @discord.ui.button(label='Enter Deck', style=discord.ButtonStyle.primary, row=0)
    async def enter_deck_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ):
        modal = DeckInputModal(
            session=self.session,
            player_index=self.player_index,
            card_index=self.card_index,
            parent_view=self,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Go Back', style=discord.ButtonStyle.secondary, row=0)
    async def go_back_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ):
        from zutomayo.ui.deck_management_views import DeckSourceView

        view = DeckSourceView(
            self.session, self.player_index, self.all_cards,
            self.card_index, self.opponent_name,
        )
        await interaction.response.edit_message(
            content=(
                '**Deck Building [デッキ構築]**\n'
                'Choose how to build your deck:\n'
                '**Build a Deck** - Enter cards manually\n'
                '**Select a Deck** - Use one of your saved decks\n'
                '**Select a Default Deck** - Use a pre-built deck'
            ),
            view=view,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.player_index not in self.session.pending_actions:
            pool = self.all_cards * 2
            random.shuffle(pool)
            deck: list[Card] = []
            counts: dict[tuple[int, int], int] = {}
            for card in pool:
                key = (card.pack, card.id)
                if counts.get(key, 0) < 2 and len(deck) < 20:
                    deck.append(card)
                    counts[key] = counts.get(key, 0) + 1
            self.session.submit_action(self.player_index, deck)
