"""Construction and behaviour smoke tests for the draft views.

These exercise the picker's option building, page slicing, the cross-page
selection diff with copy-limit enforcement, Confirm gating, and the TCG
main/side split, all without a live Discord connection (fake interactions and
a fake session stand in for the gateway).
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import discord  # noqa: E402

from zutomayo.data.deck_validator import get_card_index  # noqa: E402
from zutomayo.ui.draft_views import (  # noqa: E402
    DraftCardPickerView,
    DraftPackSelectionView,
    build_draft_card_option,
)

from tests.support.game_state_builder import card_by_identity  # noqa: E402


class FakeResponse:
    def __init__(self) -> None:
        self.edited: dict | None = None
        self.deferred = False

    async def edit_message(self, **kwargs) -> None:
        self.edited = kwargs

    async def defer(self) -> None:
        self.deferred = True


class FakeInteraction:
    def __init__(self, values: list[str] | None = None) -> None:
        self.data = {'values': values or []}
        self.response = FakeResponse()


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def display_name(self, session, player_index) -> str:
        return 'Tester'

    async def send_to_player(self, session, player_index, **kwargs) -> None:
        self.sent.append(('player', player_index, kwargs))

    async def send_to_channel(self, session, **kwargs) -> None:
        self.sent.append(('channel', kwargs))


class FakeSession:
    def __init__(self) -> None:
        self.game_id = 'draft-test'
        self.player_deck_names = {0: None, 1: None}
        self.pending_actions: dict = {}
        self.transport = FakeTransport()
        self.draft_visibility = 'private'

    def submit_action(self, player_index, action) -> None:
        self.pending_actions[player_index] = action


async def _noop_confirm(interaction, selected_cards) -> None:  # pragma: no cover - unused body
    return None


def _distinct_pool(size: int) -> list:
    all_cards, _ = get_card_index()
    return list(all_cards[:size])


def _picker(pool_cards, target_count, is_box_mode=False):
    return DraftCardPickerView(
        session=FakeSession(),
        player_index=0,
        pool_cards=pool_cards,
        target_count=target_count,
        opponent_name='Opp',
        is_box_mode=is_box_mode,
        prompt_title='Pick cards',
        on_confirm=_noop_confirm,
    )


def test_option_values_are_unique_even_for_duplicate_cards():
    card = card_by_identity('01-013')
    picker = _picker([card, card, card], target_count=2)
    assert len(set(picker.value_order)) == 3
    assert all(picker.value_to_card[value] is card for value in picker.value_order)


def test_page_options_match_the_pool_slice_in_order():
    pool = _distinct_pool(30)
    picker = _picker(pool, target_count=20)
    assert picker.total_pages == 2

    first_page = picker._page_values(0)
    assert first_page == [
        f'{card.pack:02d}-{card.id:03d}#{index}' for index, card in enumerate(pool[:25])
    ]
    select = next(item for item in picker.children if isinstance(item, discord.ui.Select))
    assert [option.value for option in select.options] == first_page


def test_option_label_is_truncated_to_one_hundred_characters():
    long_named = dataclasses.replace(card_by_identity('01-013'), name='x' * 200)
    option = build_draft_card_option(long_named, 'value', selected=False)
    assert len(option.label) == 100
    assert option.label.endswith('...')


def test_selected_options_render_as_default():
    pool = _distinct_pool(5)
    picker = _picker(pool, target_count=2)
    picker.selected_values = {picker.value_order[0]}
    picker._rebuild()
    select = next(item for item in picker.children if isinstance(item, discord.ui.Select))
    defaults = [option.value for option in select.options if option.default]
    assert defaults == [picker.value_order[0]]


def test_confirm_button_is_gated_on_exact_target_count():
    pool = _distinct_pool(5)
    picker = _picker(pool, target_count=2)

    confirm = next(
        item for item in picker.children
        if isinstance(item, discord.ui.Button) and item.label == 'Confirm'
    )
    assert confirm.disabled is True

    picker.selected_values = set(picker.value_order[:2])
    picker._rebuild()
    confirm = next(
        item for item in picker.children
        if isinstance(item, discord.ui.Button) and item.label == 'Confirm'
    )
    assert confirm.disabled is False


def test_select_callback_drops_third_copy_and_warns():
    card = card_by_identity('01-013')
    picker = _picker([card, card, card], target_count=2)

    interaction = FakeInteraction(values=list(picker.value_order))
    asyncio.run(picker._on_select(interaction))

    assert len(picker.selected_values) == 2
    assert picker._warning is not None
    assert 'max 2' in picker._warning


def test_select_callback_preserves_off_page_selection():
    pool = _distinct_pool(30)
    picker = _picker(pool, target_count=20)

    off_page_value = picker.value_order[26]  # lives on page 1
    picker.selected_values = {off_page_value}

    # Now interact with page 0 and choose its first two cards.
    page_zero_choice = picker.value_order[:2]
    interaction = FakeInteraction(values=page_zero_choice)
    asyncio.run(picker._on_select(interaction))

    assert off_page_value in picker.selected_values
    assert set(page_zero_choice).issubset(picker.selected_values)
    assert len(picker.selected_values) == 3


def test_standard_confirm_submits_twenty_cards_as_draft():
    session = FakeSession()
    pack_view = DraftPackSelectionView(
        session=session, player_index=0, num_boxes=1,
        mode='standard', target_count=20, opponent_name='Opp',
    )
    pool = _distinct_pool(20)
    finish = pack_view._build_pick_confirm(pool)

    asyncio.run(finish(FakeInteraction(), pool))

    assert session.player_deck_names[0] == '<draft>'
    assert session.pending_actions[0] == pool
    assert len(session.pending_actions[0]) == 20


def test_picker_confirm_invokes_on_confirm_with_selected_cards():
    session = FakeSession()
    pack_view = DraftPackSelectionView(
        session=session, player_index=0, num_boxes=1,
        mode='standard', target_count=20, opponent_name='Opp',
    )
    pool = _distinct_pool(25)
    picker = DraftCardPickerView(
        session=session, player_index=0, pool_cards=pool, target_count=20,
        opponent_name='Opp', is_box_mode=False, prompt_title='Build your deck (20)',
        on_confirm=pack_view._build_pick_confirm(pool),
    )
    picker.selected_values = set(picker.value_order[:20])
    picker._rebuild()

    asyncio.run(picker._confirm(FakeInteraction()))

    assert session.player_deck_names[0] == '<draft>'
    assert len(session.pending_actions[0]) == 20


def test_tcg_finish_splits_main_and_side_decks():
    session = FakeSession()
    pack_view = DraftPackSelectionView(
        session=session, player_index=0, num_boxes=1,
        mode='tcg', target_count=28, opponent_name='Opp',
    )
    # 28 cards including a duplicated card, to prove multiset removal.
    duplicated = card_by_identity('01-013')
    picked = [duplicated, duplicated] + _distinct_pool(28)[2:28]
    assert len(picked) == 28

    finish = pack_view._build_tcg_finish(picked)
    side_cards = picked[:8]
    asyncio.run(finish(FakeInteraction(), side_cards))

    payload = session.pending_actions[0]
    assert session.player_deck_names[0] == '<draft>'
    assert len(payload['deck']) == 20
    assert len(payload['side_deck']) == 8
    # Every side card came out of the picked pool; main is the remainder.
    assert payload['side_deck'] == side_cards
