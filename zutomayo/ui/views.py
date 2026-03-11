from __future__ import annotations
from typing import TYPE_CHECKING, Callable
import discord
from zutomayo.enums.card_type import CardType
from zutomayo.models.card_instance import CardInstance
from zutomayo.ui.embeds import ATTRIBUTE_EN, ATTRIBUTE_JP, CARD_TYPE_LABEL

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession


def _build_select_option(card_instance: CardInstance) -> tuple[str, str]:
    """Return (label, description) for a card select menu option."""
    card = card_instance.card
    attr_en = ATTRIBUTE_EN.get(card.attribute.value, card.attribute.value)
    attr_jp = ATTRIBUTE_JP.get(card.attribute.value, '')
    type_label = CARD_TYPE_LABEL.get(card.card_type, '')

    label = f'{card.name} [{card.name_jp}]'

    rarity = card.rarity.value

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

    return label[:100], description[:100]


class CardSelectView(discord.ui.View):
    """Dropdown to select card(s) from hand. Used for setting cards and choosing initial battle card."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        cards: list[CardInstance],
        min_cards: int = 1,
        max_cards: int = 1,
        placeholder: str = 'Select a card...',
        embed: discord.Embed | None = None,
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.min_cards = min_cards
        self.max_cards = max_cards
        self.placeholder = placeholder
        self.embed = embed
        self.opponent_name = opponent_name
        self.selected_cards: list[CardInstance] = []

        self._build_dropdown()

    def _build_dropdown(self) -> None:
        options = []
        for i, card_instance in enumerate(self.cards):
            label, description = _build_select_option(card_instance)
            options.append(discord.SelectOption(
                label=label,
                description=description,
                value=str(i),
            ))

        select = discord.ui.Select(
            placeholder=self.placeholder,
            min_values=self.min_cards,
            max_values=self.max_cards,
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_indices = [int(v) for v in interaction.data['values']]
        self.selected_cards = [self.cards[i] for i in selected_indices]
        card_names = ', '.join(card_instance.card.name for card_instance in self.selected_cards)

        # Show confirmation buttons
        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)

        reselect_btn = discord.ui.Button(label='Select Again', style=discord.ButtonStyle.grey)
        reselect_btn.callback = self._reselect_callback
        self.add_item(reselect_btn)

        await interaction.response.edit_message(
            content=f'You selected: **{card_names}**. Confirm or select again?',
            embed=None,
            view=self,
        )

    async def _confirm_callback(self, interaction: discord.Interaction):
        self.session.submit_action(self.player_index, self.selected_cards)
        card_names = ', '.join(card_instance.card.name for card_instance in self.selected_cards)
        await interaction.response.edit_message(
            content=f'You selected: **{card_names}**. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _reselect_callback(self, interaction: discord.Interaction):
        self.selected_cards = []
        self.clear_items()
        self._build_dropdown()
        await interaction.response.edit_message(
            content='Select your card(s):',
            embed=self.embed,
            view=self,
        )


class TwoStepCardSelectView(discord.ui.View):
    """
    Two-step sequential dropdown for selecting up to 2 cards.

    Step 1: Single-select from all cards in hand.
    Step 2: Single-select from remaining cards + a 'None' option.
    Calls session.submit_action() only after step 2 completes.
    """

    NONE_SENTINEL = '__NONE__'

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        cards: list[CardInstance],
        embed: discord.Embed | None = None,
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.embed = embed
        self.opponent_name = opponent_name
        self.first_card: CardInstance | None = None
        self.selected_cards: list[CardInstance] = []

        self._build_step1_dropdown()

    def _build_step1_dropdown(self) -> None:
        options = []
        for i, card_instance in enumerate(self.cards):
            label, description = _build_select_option(card_instance)
            options.append(discord.SelectOption(
                label=label,
                description=description,
                value=str(i),
            ))

        select = discord.ui.Select(
            placeholder='Select your 1st card to set...',
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self._step1_callback
        self.add_item(select)

    async def _step1_callback(self, interaction: discord.Interaction) -> None:
        index = int(interaction.data['values'][0])
        self.first_card = self.cards[index]

        # Clear step-1 dropdown and build step-2
        self.clear_items()

        options = []
        for i, card_instance in enumerate(self.cards):
            if i == index:
                continue
            label, description = _build_select_option(card_instance)
            options.append(discord.SelectOption(
                label=label,
                description=description,
                value=str(i),
            ))

        options.append(discord.SelectOption(
            label='None - play only 1 card',
            description='Skip the 2nd card.',
            value=self.NONE_SENTINEL,
        ))

        select = discord.ui.Select(
            placeholder='Select your 2nd card (or None)...',
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self._step2_callback
        self.add_item(select)

        first_name = self.first_card.card.name
        await interaction.response.edit_message(
            content=f'1st card: **{first_name}**. Now select a 2nd card or "None".',
            embed=self.embed,
            view=self,
        )

    async def _step2_callback(self, interaction: discord.Interaction) -> None:
        value = interaction.data['values'][0]

        if value == self.NONE_SENTINEL:
            self.selected_cards = [self.first_card]
        else:
            second_index = int(value)
            self.selected_cards = [self.first_card, self.cards[second_index]]

        card_names = ', '.join(card_instance.card.name for card_instance in self.selected_cards)

        # Show confirmation buttons
        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)

        reselect_btn = discord.ui.Button(label='Select Again', style=discord.ButtonStyle.grey)
        reselect_btn.callback = self._reselect_callback
        self.add_item(reselect_btn)

        await interaction.response.edit_message(
            content=f'You selected: **{card_names}**. Confirm or select again?',
            embed=None,
            view=self,
        )

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        self.session.submit_action(self.player_index, self.selected_cards)
        card_names = ', '.join(card_instance.card.name for card_instance in self.selected_cards)
        await interaction.response.edit_message(
            content=f'You selected: **{card_names}**. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _reselect_callback(self, interaction: discord.Interaction) -> None:
        self.first_card = None
        self.selected_cards = []
        self.clear_items()
        self._build_step1_dropdown()
        await interaction.response.edit_message(
            content='Select your card(s):',
            embed=self.embed,
            view=self,
        )


class RedrawView(discord.ui.View):
    """View for redraw phase - select cards to redraw or keep hand."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        cards: list[CardInstance],
        opponent_name: str = 'opponent',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.opponent_name = opponent_name
        self.selected_cards: list[CardInstance] = []

        self._build_initial()

    # -- state builders --------------------------------------------------

    def _build_initial(self) -> None:
        keep_btn = discord.ui.Button(label='Keep Hand', style=discord.ButtonStyle.green)
        keep_btn.callback = self._keep_hand_pressed
        self.add_item(keep_btn)

        discard_btn = discord.ui.Button(label='Discard Cards', style=discord.ButtonStyle.danger)
        discard_btn.callback = self._discard_cards_pressed
        self.add_item(discard_btn)

    def _build_discard_select(self) -> None:
        if self.cards:
            options = []
            for i, card_instance in enumerate(self.cards):
                label, description = _build_select_option(card_instance)
                options.append(discord.SelectOption(
                    label=label,
                    description=description,
                    value=str(i),
                ))

            select = discord.ui.Select(
                placeholder='Select cards to discard...',
                min_values=1,
                max_values=len(self.cards),
                options=options,
            )
            select.callback = self._discard_select_callback
            self.add_item(select)

        back_btn = discord.ui.Button(label='Go Back', style=discord.ButtonStyle.grey)
        back_btn.callback = self._go_back_to_initial
        self.add_item(back_btn)

    # -- callbacks --------------------------------------------------------

    async def _keep_hand_pressed(self, interaction: discord.Interaction):
        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_btn.callback = self._keep_hand_confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label='Go Back', style=discord.ButtonStyle.grey)
        back_btn.callback = self._go_back_to_initial
        self.add_item(back_btn)

        await interaction.response.edit_message(
            content='Keep your current hand? Confirm or go back.',
            view=self,
        )

    async def _keep_hand_confirm(self, interaction: discord.Interaction):
        self.session.submit_action(self.player_index, {'redraw': []})
        await interaction.response.edit_message(
            content=f'Keeping your hand. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _discard_cards_pressed(self, interaction: discord.Interaction):
        self.selected_cards = []
        self.clear_items()
        self._build_discard_select()
        await interaction.response.edit_message(
            content='Select cards to discard, then confirm.',
            view=self,
        )

    async def _discard_select_callback(self, interaction: discord.Interaction):
        selected_indices = [int(v) for v in interaction.data['values']]
        self.selected_cards = [self.cards[i] for i in selected_indices]
        card_names = ', '.join(card_instance.card.name for card_instance in self.selected_cards)

        self.clear_items()

        confirm_btn = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_btn.callback = self._discard_confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label='Go Back', style=discord.ButtonStyle.grey)
        back_btn.callback = self._go_back_to_discard_select
        self.add_item(back_btn)

        await interaction.response.edit_message(
            content=f'Discard **{card_names}**? Confirm or go back to reselect.',
            view=self,
        )

    async def _discard_confirm(self, interaction: discord.Interaction):
        self.session.submit_action(self.player_index, {'redraw': self.selected_cards})
        count = len(self.selected_cards)
        await interaction.response.edit_message(
            content=f'Redrawing **{count}** card(s)... Waiting for {self.opponent_name}.',
            view=None,
        )
        self.stop()

    # -- go-back helpers --------------------------------------------------

    async def _go_back_to_initial(self, interaction: discord.Interaction):
        self.selected_cards = []
        self.clear_items()
        self._build_initial()
        await interaction.response.edit_message(
            content='Select the cards you want to redraw [最初の手札を引いたとき、一度だけ引き直しができます。]',
            view=self,
        )

    async def _go_back_to_discard_select(self, interaction: discord.Interaction):
        self.selected_cards = []
        self.clear_items()
        self._build_discard_select()
        await interaction.response.edit_message(
            content='Select cards to discard, then confirm.',
            view=self,
        )


class EffectCardSelectView(discord.ui.View):
    """Dropdown to select a card during effect resolution (e.g. from Abyss or Power Charger)."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        cards: list[CardInstance],
        placeholder: str = 'Select a card...',
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.cards = cards

        options = []
        for i, card_instance in enumerate(cards):
            label, description = _build_select_option(card_instance)
            options.append(discord.SelectOption(
                label=label,
                description=description,
                value=str(i),
            ))

        select = discord.ui.Select(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_index = int(interaction.data['values'][0])
        selected_card = self.cards[selected_index]
        self.session.submit_action(self.player_index, selected_card)
        await interaction.response.edit_message(
            content=f'You selected: **{selected_card.card.name}**',
            view=None,
        )
        self.stop()


class EffectNumberSelectView(discord.ui.View):
    """Dropdown to select a number during effect resolution (e.g. clock advancement 0-5)."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        min_value: int,
        max_value: int,
        placeholder: str = 'Select a number...',
        label_prefix: str | None = None,
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index

        options = [
            discord.SelectOption(
                label=f'{label_prefix} {n}' if label_prefix else str(n),
                value=str(n),
            )
            for n in range(min_value, max_value + 1)
        ]

        select = discord.ui.Select(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = int(interaction.data['values'][0])
        self.session.submit_action(self.player_index, selected_value)
        await interaction.response.edit_message(
            content=f'You selected: **{selected_value}**',
            view=None,
        )
        self.stop()


class EffectTextInputModal(discord.ui.Modal):
    """Modal with a text input field for effects that require the player to type a value."""

    name_input = discord.ui.TextInput(
        label='Card ID',
        placeholder='Enter a card ID (XX-XXX)...',
        required=True,
        max_length=100,
    )

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        title: str = 'Specify a card',
        label: str | None = None,
        placeholder: str | None = None,
        validator: Callable[[str], str | None] | None = None,
        prompt_text: str | None = None,
    ):
        super().__init__(title=title, timeout=120)
        self.session = session
        self.player_index = player_index
        self.validator = validator
        self.prompt_text = prompt_text
        if label is not None:
            self.name_input.label = label
        if placeholder is not None:
            self.name_input.placeholder = placeholder

    async def on_submit(self, interaction: discord.Interaction):
        value = self.name_input.value.strip()
        if self.validator is not None:
            error = self.validator(value)
            if error is not None:
                view = EffectTextInputView(
                    self.session,
                    self.player_index,
                    modal_title=self.title,
                    label=self.name_input.label,
                    placeholder=self.name_input.placeholder,
                    validator=self.validator,
                    prompt_text=self.prompt_text,
                )
                await interaction.response.send_message(content=error, view=view, ephemeral=True)
                return
        self.session.submit_action(self.player_index, value)
        await interaction.response.send_message(
            content=f'You specified: **{value}**',
            ephemeral=True,
        )


class EffectTextInputView(discord.ui.View):
    """View with a button that opens a text input modal for specifying a value."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        modal_title: str = 'Specify a card',
        button_label: str = 'Enter Card ID',
        label: str | None = None,
        placeholder: str | None = None,
        validator: Callable[[str], str | None] | None = None,
        prompt_text: str | None = None,
    ):
        super().__init__(timeout=300)
        self.session = session
        self.player_index = player_index
        self.modal_title = modal_title
        self.input_label = label
        self.input_placeholder = placeholder
        self.validator = validator
        self.prompt_text = prompt_text
        self.open_modal.label = button_label

    @discord.ui.button(label='Enter Card ID', style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = EffectTextInputModal(
            self.session,
            self.player_index,
            title=self.modal_title,
            label=self.input_label,
            placeholder=self.input_placeholder,
            validator=self.validator,
            prompt_text=self.prompt_text,
        )
        await interaction.response.send_modal(modal)
        self.stop()


class GameLobbyView(discord.ui.View):
    """Join button shown in server channel when a game is created."""

    def __init__(self, game_id: str):
        super().__init__(timeout=600)
        self.game_id = game_id

    @discord.ui.button(label='Join Game', style=discord.ButtonStyle.green)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from zutomayo.engine.game_session import session_manager

        try:
            session = session_manager.join_game(self.game_id, interaction.user.id)
            button.disabled = True
            button.label = 'Game Full'
            await interaction.response.edit_message(
                content=f'**{interaction.user.display_name}** joined the game! Starting...',
                view=self,
            )
            # Start the game flow
            if session.is_tcg:
                from zutomayo.engine.tcg_match_flow import TcgMatchFlow
                flow = TcgMatchFlow(interaction.client, session.best_of)
                session.game_task = interaction.client.loop.create_task(
                    flow.run_tcg(session)
                )
            else:
                from zutomayo.engine.game_flow import GameFlow
                game_flow = GameFlow(interaction.client)
                session.game_task = interaction.client.loop.create_task(
                    game_flow.run_game(session)
                )
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
