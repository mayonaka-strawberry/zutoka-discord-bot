from __future__ import annotations
import re
from typing import TYPE_CHECKING, Callable
import discord
from zutomayo.enums.card_type import CardType
from zutomayo.match.card_names import (
    OPTION_TEXT_LIMIT,
    card_name_description,
    card_name_label,
    search_card_names,
)
from zutomayo.ui.embeds import ATTRIBUTE_EN, ATTRIBUTE_JP, CARD_TYPE_LABEL

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.match.card_names import CardNameEntry, CardNameSearchResult

# Views answer through submit_callback (broker decisions, JSON-serializable payloads) or,
# without one, session.submit_action with live objects (deck building and TCG deck
# selection, which happen before the game record exists). Broker callbacks return
# whether the answer was accepted.
SubmitCallback = Callable[[str, object], bool | None]

PAYLOAD_INDICES = 'indices'
PAYLOAD_NUMBER = 'number'

PROMPT_EXPIRED_TEXT = 'This prompt has expired.'
SEARCH_QUERY_LIMIT = 100
# Escaped in echoed text; discord.utils.escape_markdown warns on Python 3.14.
_MARKDOWN_CHARACTERS = re.compile(r'([\\*_~|`>\[\]])')


def _build_select_option(card_holder) -> tuple[str, str]:
    """Return (label, description) for a card select menu option. Accepts any
    object with a `.card` attribute (CardView or similar)."""
    card = card_holder.card
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
    """Dropdown to select card(s) from hand."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        cards: list,
        min_cards: int = 1,
        max_cards: int = 1,
        placeholder: str = 'Select a card...',
        embed: discord.Embed | None = None,
        opponent_name: str = 'opponent',
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.min_cards = min_cards
        self.max_cards = max_cards
        self.placeholder = placeholder
        self.embed = embed
        self.opponent_name = opponent_name
        self.submit_callback = submit_callback
        self.selected_cards: list = []
        self.selected_indices: list[int] = []

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
        self.selected_indices = [int(v) for v in interaction.data['values']]
        self.selected_cards = [self.cards[i] for i in self.selected_indices]
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
        if self.submit_callback is not None:
            self.submit_callback(PAYLOAD_INDICES, self.selected_indices)
        else:
            self.session.submit_action(self.player_index, self.selected_cards)
        card_names = ', '.join(card_instance.card.name for card_instance in self.selected_cards)
        await interaction.response.edit_message(
            content=f'You selected: **{card_names}**. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _reselect_callback(self, interaction: discord.Interaction):
        self.selected_cards = []
        self.selected_indices = []
        self.clear_items()
        self._build_dropdown()
        await interaction.response.edit_message(
            content='Select your card(s):',
            embed=self.embed,
            view=self,
        )


class TwoStepCardSelectView(discord.ui.View):
    """Two dropdowns for up to 2 cards: one from the hand, then one from the rest or 'None'.
    Submits after step 2."""

    NONE_SENTINEL = '__NONE__'

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        cards: list,
        embed: discord.Embed | None = None,
        opponent_name: str = 'opponent',
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.embed = embed
        self.opponent_name = opponent_name
        self.submit_callback = submit_callback
        self.first_card: object | None = None
        self.selected_cards: list = []
        self.selected_indices: list[int] = []

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

        first_index = self.cards.index(self.first_card)
        if value == self.NONE_SENTINEL:
            self.selected_cards = [self.first_card]
            self.selected_indices = [first_index]
        else:
            second_index = int(value)
            self.selected_cards = [self.first_card, self.cards[second_index]]
            self.selected_indices = [first_index, second_index]

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
        if self.submit_callback is not None:
            self.submit_callback(PAYLOAD_INDICES, self.selected_indices)
        else:
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
        self.selected_indices = []
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
        cards: list,
        opponent_name: str = 'opponent',
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.opponent_name = opponent_name
        self.submit_callback = submit_callback
        self.selected_cards: list = []
        self.selected_indices: list[int] = []

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
        if self.submit_callback is not None:
            self.submit_callback(PAYLOAD_INDICES, [])
        else:
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
        self.selected_indices = [int(v) for v in interaction.data['values']]
        self.selected_cards = [self.cards[i] for i in self.selected_indices]
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
        if self.submit_callback is not None:
            self.submit_callback(PAYLOAD_INDICES, self.selected_indices)
        else:
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
        self.selected_indices = []
        self.clear_items()
        self._build_initial()
        await interaction.response.edit_message(
            content='Select the cards you want to redraw [最初の手札を引いたとき、一度だけ引き直しができます。]',
            view=self,
        )

    async def _go_back_to_discard_select(self, interaction: discord.Interaction):
        self.selected_cards = []
        self.selected_indices = []
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
        cards: list,
        placeholder: str = 'Select a card...',
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.submit_callback = submit_callback

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
        if self.submit_callback is not None:
            self.submit_callback(PAYLOAD_INDICES, [selected_index])
        else:
            self.session.submit_action(self.player_index, selected_card)
        await interaction.response.edit_message(
            content=f'You selected: **{selected_card.card.name}**',
            view=None,
        )
        self.stop()


class EffectNumberSelectView(discord.ui.View):
    """Dropdown to select a number during effect resolution (e.g. clock advancement 0-5).
    With `slot_positions` it picks a hidden hand card: the options are Card 1..N and
    the view submits the position behind the chosen slot."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        min_value: int,
        max_value: int,
        placeholder: str = 'Select a number...',
        label_prefix: str | None = None,
        submit_callback: SubmitCallback | None = None,
        slot_positions: list[int] | None = None,
        timeout: float = 750,
    ):
        super().__init__(timeout=timeout)
        self.session = session
        self.player_index = player_index
        self.submit_callback = submit_callback
        # Display slot N (from 1) picks slot_positions[N - 1]; only slots reach Discord.
        self.slot_positions = slot_positions
        self.answered = False

        if slot_positions is None:
            options = [
                discord.SelectOption(
                    label=f'{label_prefix} {n}' if label_prefix else str(n),
                    value=str(n),
                )
                for n in range(min_value, max_value + 1)
            ]
        else:
            options = [
                discord.SelectOption(label=f'Card {slot}', value=str(slot))
                for slot in range(1, len(slot_positions) + 1)
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
        if self.answered:
            await interaction.response.defer()
            return
        # Set before any await, so a later pick is deferred and cannot replace the shown
        # answer. Stopping comes after the edit: a stopped view drops clicks unanswered.
        self.answered = True
        try:
            selected_value = int(interaction.data['values'][0])
            content = f'You selected: **{selected_value}**'
            if self.slot_positions is not None:
                accepted = self.submit_callback(
                    PAYLOAD_NUMBER, self.slot_positions[selected_value - 1])
                content = f'You chose Card {selected_value}.' if accepted else PROMPT_EXPIRED_TEXT
            elif self.submit_callback is not None:
                self.submit_callback(PAYLOAD_NUMBER, selected_value)
            else:
                self.session.submit_action(self.player_index, selected_value)
            await interaction.response.edit_message(content=content, view=None)
        finally:
            self.stop()


def _search_result_text(query: str, result: CardNameSearchResult) -> str:
    """The results line; the echoed query is capped and escaped."""
    echoed = _MARKDOWN_CHARACTERS.sub(r'\\\1', query[:SEARCH_QUERY_LIMIT])
    if not result.entries:
        return (f'No card name matches "{echoed}". Try part of the English or Japanese name, '
                'the song, or a number like 03-047.')
    shown = len(result.entries)
    if result.is_fuzzy:
        if result.total > shown:
            return (f'Closest names to "{echoed}": showing {shown} of {result.total}. '
                    'Add more of the name, such as the song.')
        return f'Closest names to "{echoed}":'
    if result.total > shown:
        return (f'Showing {shown} of {result.total} names for "{echoed}". '
                'Add more of the name, such as the song.')
    if result.total == 1:
        return f'1 name matches "{echoed}".'
    return f'{result.total} names match "{echoed}".'


class CardNameSearchModal(discord.ui.Modal):
    """The search box of a CardNameDeclarationView; results replace the view's message."""

    query_input = discord.ui.TextInput(
        label='Card name or number',
        placeholder='nira humanoid / にらちゃん / 03-047',
        required=True,
        max_length=SEARCH_QUERY_LIMIT,
    )

    def __init__(self, declaration_view: CardNameDeclarationView):
        super().__init__(title='Name a card', timeout=declaration_view.timeout)
        self.declaration_view = declaration_view
        if declaration_view.last_query:
            self.query_input.default = declaration_view.last_query

    async def on_submit(self, interaction: discord.Interaction):
        await self.declaration_view.show_search_results(interaction, self.query_input.value)


class CardNameDeclarationView(discord.ui.View):
    """Names a card for a name-guess effect in one message: search in a modal, pick a
    name from the results, then Declare. Submits the name's representative definition,
    which stands for every printing. `submit_callback` returns whether it was accepted;
    `is_pending` turns False once the broker's timeout fallback has answered."""

    def __init__(
        self,
        session: GameSession,
        player_index: int,
        prompt_text: str,
        submit_callback: SubmitCallback,
        timeout: float,
        is_pending: Callable[[], bool],
    ):
        super().__init__(timeout=timeout)
        self.session = session
        self.player_index = player_index
        self.prompt_text = prompt_text
        self.submit_callback = submit_callback
        self.is_pending = is_pending
        self.last_query = ''
        self.result_text = ''
        self.result_entries: tuple[CardNameEntry, ...] = ()
        self.selected_entry: CardNameEntry | None = None
        self.name_select: discord.ui.Select | None = None
        self.declare_button: discord.ui.Button | None = None
        self.declared = False
        self.closing_text: str | None = None
        self._build_components()

    def _build_components(self) -> None:
        self.clear_items()
        self.name_select = None
        self.declare_button = None
        if self.result_entries:
            self.name_select = discord.ui.Select(
                placeholder='Choose a card name...',
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=card_name_label(entry.representative)[:OPTION_TEXT_LIMIT],
                        description=card_name_description(entry),
                        value=str(entry.representative),
                        default=entry == self.selected_entry,
                    )
                    for entry in self.result_entries
                ],
            )
            self.name_select.callback = self._select_callback
            self.add_item(self.name_select)
            self.declare_button = discord.ui.Button(
                label='Declare',
                style=discord.ButtonStyle.success,
                disabled=self.selected_entry is None,
            )
            self.declare_button.callback = self._declare_callback
            self.add_item(self.declare_button)
        searched = bool(self.result_text)
        search_button = discord.ui.Button(
            label='Search again' if searched else 'Search card names',
            style=discord.ButtonStyle.secondary if searched else discord.ButtonStyle.primary,
        )
        search_button.callback = self._search_callback
        self.add_item(search_button)

    def _message_content(self) -> str:
        content = self.prompt_text
        if self.result_text:
            content += f'\n\n{self.result_text}'
        if self.selected_entry is not None:
            content += f'\nDeclare **{card_name_label(self.selected_entry.representative)}**?'
        return content

    async def _close_as_expired(self, interaction: discord.Interaction) -> None:
        """The broker no longer waits: close the view, which every click would keep alive."""
        try:
            await interaction.response.edit_message(content=PROMPT_EXPIRED_TEXT, view=None)
        finally:
            self.stop()

    async def _search_callback(self, interaction: discord.Interaction):
        if self.declared:
            await interaction.response.defer()
            return
        if not self.is_pending():
            await self._close_as_expired(interaction)
            return
        # The view keeps running while the modal is open.
        await interaction.response.send_modal(CardNameSearchModal(self))

    async def show_search_results(self, interaction: discord.Interaction, query: str) -> None:
        """Modal submit: show the results for `query` in this message."""
        if self.declared or self.is_finished():
            await interaction.response.edit_message(
                content=self.closing_text or PROMPT_EXPIRED_TEXT,
                view=None,
            )
            return
        if not self.is_pending():
            await self._close_as_expired(interaction)
            return
        query = query.strip()
        result = search_card_names(query)
        self.last_query = query
        self.result_text = _search_result_text(query, result)
        self.result_entries = result.entries
        # A card number or a verbatim full name is preselected.
        self.selected_entry = result.exact_entry
        self._build_components()
        await interaction.response.edit_message(content=self._message_content(), view=self)

    async def _select_callback(self, interaction: discord.Interaction):
        value = interaction.data['values'][0]
        entry = next(
            (entry for entry in self.result_entries if str(entry.representative) == value),
            None,
        )
        if self.declared or entry is None:
            await interaction.response.defer()
            return
        if not self.is_pending():
            await self._close_as_expired(interaction)
            return
        self.selected_entry = entry
        for option in self.name_select.options:
            option.default = option.value == value
        self.declare_button.disabled = False
        await interaction.response.edit_message(content=self._message_content(), view=self)

    async def _declare_callback(self, interaction: discord.Interaction):
        entry = self.selected_entry
        if self.declared or entry is None:
            await interaction.response.defer()
            return
        # Set before any await, so later clicks are deferred and change nothing. Stopping
        # comes after the edit: a stopped view drops clicks unanswered.
        self.declared = True
        try:
            if self.submit_callback('action', entry.representative):
                self.closing_text = f'You named **{card_name_label(entry.representative)}**.'
            else:
                self.closing_text = PROMPT_EXPIRED_TEXT
            await interaction.response.edit_message(content=self.closing_text, view=None)
        finally:
            self.stop()


class GameLobbyView(discord.ui.View):
    """Join button shown in server channel when a game is created."""

    def __init__(self, game_id: str):
        super().__init__(timeout=750)
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
                from zutomayo.match.series_flow import TcgSeriesFlow
                flow = TcgSeriesFlow(interaction.client, session.best_of)
                session.game_task = interaction.client.loop.create_task(
                    flow.run_tcg(session)
                )
            else:
                from zutomayo.match.match_flow import SingleMatchFlow
                game_flow = SingleMatchFlow(interaction.client)
                session.game_task = interaction.client.loop.create_task(
                    game_flow.run_game(session)
                )
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)


class ActionSelectView(discord.ui.View):
    """Dropdown over `cards` that submits one engine action (payload 'action').
    `allow_pass` adds a pass row (action len(cards)); `confirm` adds a confirm or
    reselect step (set cards, initial card)."""

    PASS_SENTINEL = '__PASS__'

    def __init__(
        self,
        session,
        player_index: int,
        cards: list,
        placeholder: str = 'Select a card...',
        allow_pass: bool = False,
        pass_label: str = 'Pass',
        confirm: bool = False,
        opponent_name: str = 'opponent',
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.cards = cards
        self.placeholder = placeholder
        self.allow_pass = allow_pass
        self.pass_label = pass_label
        self.confirm = confirm
        self.opponent_name = opponent_name
        self.submit_callback = submit_callback
        self.selected_action: int | None = None
        self._build_dropdown()

    def _build_dropdown(self) -> None:
        options = []
        for index, item in enumerate(self.cards):
            label, description = _build_select_option(item)
            options.append(discord.SelectOption(label=label, description=description, value=str(index)))
        if self.allow_pass:
            options.append(discord.SelectOption(label=self.pass_label, value=self.PASS_SENTINEL))
        select = discord.ui.Select(
            placeholder=self.placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self._select_callback
        self.add_item(select)

    def _selection_name(self) -> str:
        if self.selected_action is not None and self.selected_action < len(self.cards):
            return self.cards[self.selected_action].card.name
        return self.pass_label

    async def _select_callback(self, interaction: discord.Interaction):
        value = interaction.data['values'][0]
        self.selected_action = len(self.cards) if value == self.PASS_SENTINEL else int(value)

        if not self.confirm:
            self._submit()
            await interaction.response.edit_message(
                content=f'You selected: **{self._selection_name()}**',
                view=None,
            )
            self.stop()
            return

        self.clear_items()
        confirm_button = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
        confirm_button.callback = self._confirm_callback
        self.add_item(confirm_button)
        reselect_button = discord.ui.Button(label='Select Again', style=discord.ButtonStyle.grey)
        reselect_button.callback = self._reselect_callback
        self.add_item(reselect_button)
        await interaction.response.edit_message(
            content=f'You selected: **{self._selection_name()}**. Confirm or select again?',
            embed=None,
            view=self,
        )

    def _submit(self) -> None:
        if self.submit_callback is not None:
            self.submit_callback('action', self.selected_action)

    async def _confirm_callback(self, interaction: discord.Interaction):
        self._submit()
        await interaction.response.edit_message(
            content=f'You selected: **{self._selection_name()}**. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _reselect_callback(self, interaction: discord.Interaction):
        self.selected_action = None
        self.clear_items()
        self._build_dropdown()
        await interaction.response.edit_message(
            content='Select your card:',
            view=self,
        )


class BinaryChoiceView(discord.ui.View):
    """Two buttons submitting engine action 0 or 1 (payload type 'action')."""

    def __init__(
        self,
        session,
        player_index: int,
        labels: tuple[str, str] = ('No', 'Yes'),
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.submit_callback = submit_callback

        button_zero = discord.ui.Button(label=labels[0], style=discord.ButtonStyle.primary)
        button_zero.callback = self._make_callback(0, labels[0])
        self.add_item(button_zero)

        button_one = discord.ui.Button(label=labels[1], style=discord.ButtonStyle.secondary)
        button_one.callback = self._make_callback(1, labels[1])
        self.add_item(button_one)

    def _make_callback(self, action: int, label: str):
        async def callback(interaction: discord.Interaction):
            if self.submit_callback is not None:
                self.submit_callback('action', action)
            await interaction.response.edit_message(
                content=f'You selected: **{label}**',
                view=None,
            )
            self.stop()

        return callback


class ConfirmableChoiceView(discord.ui.View):
    """One button per option, then Confirm or Go Back, submitting the option's action
    (payload 'action'). For choices where a misclick should be recoverable (the TCG
    side choice). Options need `label` and `action`."""

    def __init__(
        self,
        session,
        player_index: int,
        options: list,
        prompt_text: str = '',
        opponent_name: str = 'opponent',
        submit_callback: SubmitCallback | None = None,
    ):
        super().__init__(timeout=750)
        self.session = session
        self.player_index = player_index
        self.options = options
        self.prompt_text = prompt_text
        self.opponent_name = opponent_name
        self.submit_callback = submit_callback
        self.selected_option = None
        self._build_choice_buttons()

    def _build_choice_buttons(self) -> None:
        self.clear_items()
        for position, option in enumerate(self.options):
            style = discord.ButtonStyle.primary if position == 0 else discord.ButtonStyle.secondary
            button = discord.ui.Button(label=option.label, style=style)
            button.callback = self._make_choice_callback(option)
            self.add_item(button)

    def _make_choice_callback(self, option):
        async def callback(interaction: discord.Interaction):
            self.selected_option = option
            self.clear_items()
            confirm_button = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green)
            confirm_button.callback = self._confirm_callback
            self.add_item(confirm_button)
            go_back_button = discord.ui.Button(label='Go Back', style=discord.ButtonStyle.grey)
            go_back_button.callback = self._go_back_callback
            self.add_item(go_back_button)
            await interaction.response.edit_message(
                content=f'You selected: **{option.label}**. Confirm or go back?',
                view=self,
            )

        return callback

    async def _confirm_callback(self, interaction: discord.Interaction):
        option = self.selected_option
        if self.submit_callback is not None:
            self.submit_callback('action', option.action)
        await interaction.response.edit_message(
            content=f'You selected: **{option.label}**. Waiting for {self.opponent_name}...',
            view=None,
        )
        self.stop()

    async def _go_back_callback(self, interaction: discord.Interaction):
        self.selected_option = None
        self._build_choice_buttons()
        await interaction.response.edit_message(
            content=self.prompt_text,
            view=self,
        )
