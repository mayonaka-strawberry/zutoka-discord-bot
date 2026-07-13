"""Discord views for the draft phase.

``DraftPackSelectionView`` walks a player through choosing the pack for each of
their boxes, opens the boxes, reveals them, and hands off to
``DraftCardPickerView`` for building a deck from the opened cards.

``DraftCardPickerView`` is a paginated multi-select picker used in two modes:
box mode (the pages mirror the two 25-card grid images of each opened box, with
the matching image attached) and pool mode (a plain card list, used for the TCG
side-deck pick). All draft views have no timeout; each callback first verifies
the game is still active, so clicks on a stale view after a quit or end are
handled gracefully.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Awaitable, Callable

import discord

from zutomayo.engine.draft_phase import (
    CARDS_PER_PAGE,
    MAXIMUM_COPIES_PER_CARD,
    TCG_DRAFT_SIDE_DECK_SIZE,
    box_page_title,
    enforce_copy_limit,
    format_selected_picks,
    open_draft_boxes,
    send_box_reveal,
    total_pages_for_pool,
)
from zutomayo.enums.card_type import CardType
from zutomayo.ui.embeds import (
    ATTRIBUTE_EN,
    ATTRIBUTE_JP,
    CARD_TYPE_LABEL,
    create_deck_grid_image_off_thread,
)

if TYPE_CHECKING:
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card

ConfirmCallback = Callable[[discord.Interaction, list['Card']], Awaitable[None]]


def _draft_card_description(card: 'Card') -> str:
    """Rarity/type/stats line for a card option, matching the switch-view style."""
    attribute_en = ATTRIBUTE_EN.get(card.attribute.value, card.attribute.value)
    attribute_jp = ATTRIBUTE_JP.get(card.attribute.value, '')
    type_label = CARD_TYPE_LABEL.get(card.card_type, '')
    rarity = card.rarity.value
    if card.card_type == CardType.CHARACTER:
        description = (
            f'{rarity} | {type_label} | {attribute_en} [{attribute_jp}] | CLK: {card.clock}'
            f' | N: {card.attack_night} | D: {card.attack_day}'
            f' | Cost: {card.power_cost} | STP: {card.send_to_power}'
        )
    else:
        description = (
            f'{rarity} | {type_label} | {attribute_en} [{attribute_jp}] | CLK: {card.clock}'
            f' | Cost: {card.power_cost} | STP: {card.send_to_power}'
        )
    if len(description) > 100:
        description = description[:97] + '...'
    return description


def build_draft_card_option(card: 'Card', value: str, selected: bool) -> discord.SelectOption:
    """Build a select option labelled ``[01-023] Card Name (Japanese Name)``."""
    label = f'[{card.pack:02d}-{card.id:03d}] {card.name} ({card.name_jp})'
    if len(label) > 100:
        label = label[:97] + '...'
    return discord.SelectOption(
        label=label,
        description=_draft_card_description(card),
        value=value,
        default=selected,
    )


class DraftCardPickerView(discord.ui.View):
    """Paginated multi-select picker for building a deck from an opened pool."""

    def __init__(
        self,
        session: 'GameSession',
        player_index: int,
        pool_cards: list['Card'],
        target_count: int,
        opponent_name: str,
        is_box_mode: bool,
        prompt_title: str,
        on_confirm: ConfirmCallback,
    ) -> None:
        super().__init__(timeout=None)
        self.session = session
        self.player_index = player_index
        self.pool_cards = list(pool_cards)
        self.target_count = target_count
        self.opponent_name = opponent_name
        self.is_box_mode = is_box_mode
        self.prompt_title = prompt_title
        self.on_confirm = on_confirm

        self.page = 0
        self.selected_values: set[str] = set()
        self._warning: str | None = None
        self._page_image_bytes: dict[int, bytes | None] = {}

        # Each opened copy is its own option, so duplicates get a unique value
        # via their global pool index. The option list mirrors the pool (and in
        # box mode, the grid images) exactly, so the copy limit is enforced at
        # selection time rather than by pruning the pool.
        self.value_order: list[str] = []
        self.value_to_card: dict[str, 'Card'] = {}
        for pool_index, card in enumerate(self.pool_cards):
            value = f'{card.pack:02d}-{card.id:03d}#{pool_index}'
            self.value_order.append(value)
            self.value_to_card[value] = card

        self.total_pages = total_pages_for_pool(len(self.pool_cards))
        self._rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from zutomayo.engine.game_session import session_manager

        if session_manager.active_games.get(self.session.game_id) is not self.session:
            self.stop()
            await interaction.response.edit_message(
                content='This game has ended.', embed=None, view=None, attachments=[],
            )
            return False
        return True

    def _page_values(self, page: int) -> list[str]:
        start = page * CARDS_PER_PAGE
        end = min(start + CARDS_PER_PAGE, len(self.value_order))
        return self.value_order[start:end]

    def _page_title(self) -> str:
        if self.is_box_mode:
            return box_page_title(self.page)
        return f'Page {self.page + 1}/{self.total_pages}'

    def _selected_cards(self) -> list['Card']:
        return [self.value_to_card[value] for value in self.value_order if value in self.selected_values]

    def _rebuild(self) -> None:
        self.clear_items()

        page_values = self._page_values(self.page)
        options = [
            build_draft_card_option(self.value_to_card[value], value, value in self.selected_values)
            for value in page_values
        ]
        select = discord.ui.Select(
            placeholder='Select cards on this page...',
            options=options,
            min_values=0,
            max_values=len(options),
            row=0,
        )
        select.callback = self._on_select
        self.add_item(select)

        previous_button = discord.ui.Button(
            label='<< Prev', style=discord.ButtonStyle.grey, row=1, disabled=(self.page == 0),
        )
        previous_button.callback = self._previous_page
        self.add_item(previous_button)

        next_button = discord.ui.Button(
            label='Next >>', style=discord.ButtonStyle.grey, row=1,
            disabled=(self.page >= self.total_pages - 1),
        )
        next_button.callback = self._next_page
        self.add_item(next_button)

        clear_button = discord.ui.Button(label='Clear All', style=discord.ButtonStyle.grey, row=1)
        clear_button.callback = self._clear_all
        self.add_item(clear_button)

        confirm_button = discord.ui.Button(
            label='Confirm', style=discord.ButtonStyle.green, row=1,
            disabled=(len(self.selected_values) != self.target_count),
        )
        confirm_button.callback = self._confirm
        self.add_item(confirm_button)

    def render_content(self) -> str:
        line = (
            f'**{self.prompt_title}** - {self._page_title()} - '
            f'Selected {len(self.selected_values)}/{self.target_count}'
        )
        if self._warning:
            line += f'\n{self._warning}'
        return line

    def build_embed(self) -> discord.Embed:
        selected_cards = self._selected_cards()
        embed = discord.Embed(
            title=f'Your picks ({len(selected_cards)}/{self.target_count})',
            color=discord.Color.orange(),
        )
        lines = format_selected_picks(selected_cards)
        embed.description = '\n'.join(lines) if lines else 'No cards selected yet.'
        return embed

    async def render_page_image(self, page: int) -> discord.File | None:
        """Return a fresh File for *page*'s grid image, rendering once and caching
        the bytes so later page views re-wrap them instead of re-rendering."""
        if not self.is_box_mode:
            return None
        if page in self._page_image_bytes:
            data = self._page_image_bytes[page]
            return discord.File(io.BytesIO(data), filename=f'draft_page_{page}.webp') if data else None

        start = page * CARDS_PER_PAGE
        page_cards = self.pool_cards[start:start + CARDS_PER_PAGE]
        rendered = await create_deck_grid_image_off_thread(
            page_cards, columns=5, filename=f'draft_page_{page}.webp',
        )
        if rendered is None:
            self._page_image_bytes[page] = None
            return None
        data = rendered.fp.read()
        rendered.fp.seek(0)
        self._page_image_bytes[page] = data
        return rendered

    async def _on_select(self, interaction: discord.Interaction) -> None:
        page_values = self._page_values(self.page)
        page_value_set = set(page_values)
        chosen_on_page = set(interaction.data['values'])

        # Off-page selections were already legal, so keep them first; among the
        # current page's choices, keep them in option order.
        off_page_values = self.selected_values - page_value_set
        prioritized = list(off_page_values) + [value for value in page_values if value in chosen_on_page]

        kept_values, dropped_cards = enforce_copy_limit(
            prioritized, self.value_to_card, MAXIMUM_COPIES_PER_CARD,
        )
        self.selected_values = kept_values

        if dropped_cards:
            names = ', '.join(sorted({f'{card.pack:02d}-{card.id:03d} {card.name}' for card in dropped_cards}))
            self._warning = f'Skipped extra copies (max {MAXIMUM_COPIES_PER_CARD}): {names}'
        else:
            self._warning = None

        self._rebuild()
        await interaction.response.edit_message(
            content=self.render_content(), embed=self.build_embed(), view=self,
        )

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        await self._show_page(interaction, max(0, self.page - 1))

    async def _next_page(self, interaction: discord.Interaction) -> None:
        await self._show_page(interaction, min(self.total_pages - 1, self.page + 1))

    async def _clear_all(self, interaction: discord.Interaction) -> None:
        self.selected_values = set()
        self._warning = None
        self._rebuild()
        await interaction.response.edit_message(
            content=self.render_content(), embed=self.build_embed(), view=self,
        )

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        self.page = page
        self._warning = None
        self._rebuild()
        message_kwargs: dict = {
            'content': self.render_content(),
            'embed': self.build_embed(),
            'view': self,
        }
        if self.is_box_mode:
            # Rendering a page's grid image on first visit can exceed Discord's
            # 3-second interaction window, so acknowledge the click first, then
            # edit the original message once the image is ready.
            if page not in self._page_image_bytes:
                await interaction.response.defer()
            image = await self.render_page_image(page)
            message_kwargs['attachments'] = [image] if image is not None else []
        if interaction.response.is_done():
            await interaction.edit_original_response(**message_kwargs)
        else:
            await interaction.response.edit_message(**message_kwargs)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if len(self.selected_values) != self.target_count:
            await interaction.response.defer()
            return
        selected_cards = self._selected_cards()
        self.stop()
        await self.on_confirm(interaction, selected_cards)


class DraftPackSelectionView(discord.ui.View):
    """Sequential pack picker: choose the pack for each box, then open them.

    A single select is reused once per box (rather than one select per box) so
    the layout stays within Discord's five-row component limit for up to five
    boxes plus the confirm and start-over buttons.
    """

    def __init__(
        self,
        session: 'GameSession',
        player_index: int,
        num_boxes: int,
        mode: str,
        target_count: int,
        opponent_name: str,
    ) -> None:
        super().__init__(timeout=None)
        self.session = session
        self.player_index = player_index
        self.num_boxes = num_boxes
        self.mode = mode  # 'standard' | 'tcg'
        self.target_count = target_count
        self.opponent_name = opponent_name
        self.chosen_pack_numbers: list[int] = []
        self._rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from zutomayo.engine.game_session import session_manager

        if session_manager.active_games.get(self.session.game_id) is not self.session:
            self.stop()
            await interaction.response.edit_message(
                content='This game has ended.', embed=None, view=None,
            )
            return False
        return True

    def _rebuild(self) -> None:
        self.clear_items()

        if len(self.chosen_pack_numbers) < self.num_boxes:
            box_number = len(self.chosen_pack_numbers) + 1
            options = [
                discord.SelectOption(label=f'Pack {pack_number}', value=str(pack_number))
                for pack_number in range(1, 5)
            ]
            select = discord.ui.Select(
                placeholder=f'Choose the pack for Box {box_number}...', options=options, row=0,
            )
            select.callback = self._on_pack_selected
            self.add_item(select)
            if self.chosen_pack_numbers:
                start_over_button = discord.ui.Button(
                    label='Start Over', style=discord.ButtonStyle.grey, row=1,
                )
                start_over_button.callback = self._start_over
                self.add_item(start_over_button)
        else:
            confirm_button = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.green, row=0)
            confirm_button.callback = self._confirm
            self.add_item(confirm_button)
            start_over_button = discord.ui.Button(label='Start Over', style=discord.ButtonStyle.grey, row=0)
            start_over_button.callback = self._start_over
            self.add_item(start_over_button)

    def render_content(self) -> str:
        parts = ['**Draft - Choose your packs**']
        if self.chosen_pack_numbers:
            parts.append(
                '\n'.join(
                    f'Box {index + 1}: Pack {pack_number}'
                    for index, pack_number in enumerate(self.chosen_pack_numbers)
                )
            )
        if len(self.chosen_pack_numbers) < self.num_boxes:
            box_number = len(self.chosen_pack_numbers) + 1
            parts.append(f'Box {box_number} of {self.num_boxes}: which pack does this box come from?')
        else:
            parts.append(f'All {self.num_boxes} box(es) chosen. Confirm to open your boxes.')
        return '\n'.join(parts)

    async def _on_pack_selected(self, interaction: discord.Interaction) -> None:
        self.chosen_pack_numbers.append(int(interaction.data['values'][0]))
        self._rebuild()
        await interaction.response.edit_message(content=self.render_content(), view=self)

    async def _start_over(self, interaction: discord.Interaction) -> None:
        self.chosen_pack_numbers = []
        self._rebuild()
        await interaction.response.edit_message(content=self.render_content(), view=self)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        from zutomayo.data.deck_validator import get_card_index

        self.stop()
        await interaction.response.edit_message(content='Opening your boxes...', view=None)

        all_cards, _ = get_card_index()
        player_name = (
            self.session.transport.display_name(self.session, self.player_index)
            or f'Player {self.player_index + 1}'
        )
        boxes = open_draft_boxes(self.chosen_pack_numbers, all_cards)
        for box_number, (pack_number, box_cards) in enumerate(
            zip(self.chosen_pack_numbers, boxes), start=1,
        ):
            await send_box_reveal(
                self.session, self.player_index, player_name,
                box_number, self.num_boxes, pack_number, box_cards,
            )

        pool_cards = [card for box_cards in boxes for card in box_cards]
        prompt_title = (
            f'Build your deck ({self.target_count})' if self.mode == 'standard'
            else f'Pick {self.target_count} cards'
        )
        picker = DraftCardPickerView(
            session=self.session,
            player_index=self.player_index,
            pool_cards=pool_cards,
            target_count=self.target_count,
            opponent_name=self.opponent_name,
            is_box_mode=True,
            prompt_title=prompt_title,
            on_confirm=self._build_pick_confirm(pool_cards),
        )
        first_image = await picker.render_page_image(0)
        message_kwargs: dict = {
            'content': picker.render_content(),
            'embed': picker.build_embed(),
            'view': picker,
        }
        if first_image is not None:
            message_kwargs['file'] = first_image
        await self.session.transport.send_to_player(
            self.session, self.player_index, **message_kwargs,
        )

    def _build_pick_confirm(self, pool_cards: list['Card']) -> ConfirmCallback:
        session = self.session
        player_index = self.player_index
        opponent_name = self.opponent_name

        if self.mode == 'standard':
            async def finish_standard(interaction: discord.Interaction, selected_cards: list['Card']) -> None:
                session.player_deck_names[player_index] = '<draft>'
                session.submit_action(player_index, list(selected_cards))
                await interaction.response.edit_message(
                    content=f'Deck locked in. Waiting for {opponent_name}...',
                    embed=None, view=None, attachments=[],
                )
                grid = await create_deck_grid_image_off_thread(
                    selected_cards, columns=5, filename='draft_deck.webp',
                )
                if grid is not None:
                    await session.transport.send_to_player(
                        session, player_index, content='**Your Deck (20):**', file=grid,
                    )
            return finish_standard

        async def to_side_pick(interaction: discord.Interaction, picked_cards: list['Card']) -> None:
            side_picker = DraftCardPickerView(
                session=session,
                player_index=player_index,
                pool_cards=list(picked_cards),
                target_count=TCG_DRAFT_SIDE_DECK_SIZE,
                opponent_name=opponent_name,
                is_box_mode=False,
                prompt_title=f'Choose your Side Deck ({TCG_DRAFT_SIDE_DECK_SIZE})',
                on_confirm=self._build_tcg_finish(list(picked_cards)),
            )
            await interaction.response.edit_message(
                content=side_picker.render_content(),
                embed=side_picker.build_embed(),
                view=side_picker,
                attachments=[],
            )
        return to_side_pick

    def _build_tcg_finish(self, picked_cards: list['Card']) -> ConfirmCallback:
        session = self.session
        player_index = self.player_index
        opponent_name = self.opponent_name

        async def finish_tcg(interaction: discord.Interaction, side_cards: list['Card']) -> None:
            main_cards = list(picked_cards)
            for card in side_cards:
                main_cards.remove(card)
            session.player_deck_names[player_index] = '<draft>'
            session.submit_action(player_index, {'deck': main_cards, 'side_deck': list(side_cards)})
            await interaction.response.edit_message(
                content=f'Deck locked in. Waiting for {opponent_name}...',
                embed=None, view=None, attachments=[],
            )
            main_image = await create_deck_grid_image_off_thread(
                main_cards, columns=5, filename='draft_main.webp',
            )
            side_image = await create_deck_grid_image_off_thread(
                side_cards, columns=4, filename='draft_side.webp',
            )
            if main_image is not None:
                await session.transport.send_to_player(
                    session, player_index, content='**Main Deck (20):**', file=main_image,
                )
            if side_image is not None:
                await session.transport.send_to_player(
                    session, player_index, content='**Side Deck (8):**', file=side_image,
                )
        return finish_tcg
