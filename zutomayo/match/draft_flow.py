"""Draft phase: players build decks only from the gacha boxes they open. The
views live in zutomayo.ui.draft_views.

Drafting happens before the game record exists, so it is never replayed: boxes
are opened with the global random (as in the gacha commands), not the session RNG.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from zutomayo.data.gacha import draw_gachabox
from zutomayo.ui.embeds import create_deck_grid_image_off_thread

if TYPE_CHECKING:
    import discord

    from zutomayo.engine.game_session import GameSession
    from zutomayo.match.match_flow import SingleMatchFlow
    from zutomayo.models.card import Card


CARDS_PER_BOX = 50
CARDS_PER_PAGE = 25
STANDARD_DRAFT_DECK_SIZE = 20
TCG_DRAFT_PICK_COUNT = 28
TCG_DRAFT_SIDE_DECK_SIZE = 8
MAXIMUM_COPIES_PER_CARD = 2


def open_draft_boxes(pack_numbers: list[int], all_cards: list['Card']) -> list[list['Card']]:
    """Open one gacha box per chosen pack. Each box is 50 cards."""
    return [draw_gachabox(pack_number, all_cards) for pack_number in pack_numbers]


def enforce_copy_limit(
    prioritized_values: list[str],
    value_to_card: dict[str, 'Card'],
    maximum_copies: int = MAXIMUM_COPIES_PER_CARD,
) -> tuple[set[str], list['Card']]:
    """Keep at most *maximum_copies* of each distinct card.

    *prioritized_values* lists selected option values in priority order: values
    earlier in the list are kept first, so callers put already-confirmed
    selections ahead of newly added ones. Returns the kept value set and the
    list of cards whose extra copies were dropped.
    """
    counts_by_card: dict[tuple[int, int], int] = {}
    kept_values: set[str] = set()
    dropped_cards: list['Card'] = []
    for value in prioritized_values:
        card = value_to_card[value]
        card_key = (card.pack, card.id)
        if counts_by_card.get(card_key, 0) < maximum_copies:
            counts_by_card[card_key] = counts_by_card.get(card_key, 0) + 1
            kept_values.add(value)
        else:
            dropped_cards.append(card)
    return kept_values, dropped_cards


def format_selected_picks(cards: list['Card']) -> list[str]:
    """Group selected cards into ``01-023 Card Name x2`` lines, sorted by id."""
    grouped: dict[tuple[int, int], dict] = {}
    for card in cards:
        card_key = (card.pack, card.id)
        if card_key not in grouped:
            grouped[card_key] = {'card': card, 'count': 0}
        grouped[card_key]['count'] += 1

    lines: list[str] = []
    for card_key in sorted(grouped):
        entry = grouped[card_key]
        card = entry['card']
        count = entry['count']
        suffix = f' x{count}' if count > 1 else ''
        lines.append(f'{card.pack:02d}-{card.id:03d} {card.name}{suffix}')
    return lines


def total_pages_for_pool(pool_size: int) -> int:
    """Number of 25-card pages needed to display a pool of *pool_size* cards."""
    return max(1, math.ceil(pool_size / CARDS_PER_PAGE))


def box_page_title(page: int) -> str:
    """Picker page title, e.g. ``Box 1 (2/2)``: each 50-card box spans two 25-card pages."""
    return f'Box {page // 2 + 1} ({page % 2 + 1}/2)'


async def send_box_reveal(
    session: 'GameSession',
    player_index: int,
    player_name: str,
    box_number: int,
    total_boxes: int,
    pack_number: int,
    box_cards: list['Card'],
) -> None:
    """Show one opened box as two messages, one 25-card grid each: to the owner by DM,
    and to the channel when the draft is public. Split because two grids in one DM
    would share its 10 MiB limit and lose quality. Each send gets a fresh discord.File."""
    header = f'**{player_name}** - Box {box_number} of {total_boxes} (Pack {pack_number})'
    halves = ((box_cards[:CARDS_PER_PAGE], '1'), (box_cards[CARDS_PER_PAGE:], '2'))

    async def render_half(half_cards: list['Card'], half_label: str):
        return await create_deck_grid_image_off_thread(
            half_cards, columns=5, filename=f'draft_box_{box_number}_{half_label}.jpg',
        )

    for half_cards, half_label in halves:
        half_header = f'{header} - {half_label}/2'

        rendered = await render_half(half_cards, half_label)
        if rendered is not None:
            await session.transport.send_to_player(
                session, player_index, content=half_header, files=[rendered],
            )

        if session.draft_visibility == 'public':
            rendered = await render_half(half_cards, half_label)
            if rendered is not None:
                await session.transport.send_to_channel(
                    session, content=half_header, files=[rendered],
                )


def _standard_intro(session: 'GameSession') -> str:
    return (
        '**ZUTOMAYO CARD DRAFT [ドラフト]**\n'
        f'Open {session.draft_boxes} box(es) and build a {STANDARD_DRAFT_DECK_SIZE}-card deck '
        'from only the cards you open.\n'
        'First choose the pack for each box, then pick your deck.\n'
        f'Deck rules: exactly {STANDARD_DRAFT_DECK_SIZE} cards, at most '
        f'{MAXIMUM_COPIES_PER_CARD} copies of any card.'
    )


def _tcg_intro(session: 'GameSession') -> str:
    return (
        '**ZUTOMAYO CARD TCG DRAFT [ドラフト]**\n'
        f'Open {session.draft_boxes} box(es) and pick {TCG_DRAFT_PICK_COUNT} cards from only '
        'the cards you open.\n'
        'First choose the pack for each box, then pick your cards. '
        f'Afterwards choose {TCG_DRAFT_SIDE_DECK_SIZE} of them as your side deck; the '
        f'remaining {STANDARD_DRAFT_DECK_SIZE} become your main deck.\n'
        f'Deck rules: at most {MAXIMUM_COPIES_PER_CARD} copies of any card.'
    )


async def run_standard_draft_phase(
    game_flow: 'GameFlow', session: 'GameSession',
) -> tuple[list['Card'], list['Card']]:
    """Run the standard draft for both players: ``(player_0_cards, player_1_cards)``,
    20 cards each. No timeout; quitting or ending the game cancels the wait."""
    from zutomayo.ui.draft_views import DraftPackSelectionView

    session.clear_pending()
    names = game_flow._player_names(session)

    for index in range(2):
        view = DraftPackSelectionView(
            session=session,
            player_index=index,
            num_boxes=session.draft_boxes,
            mode='standard',
            target_count=STANDARD_DRAFT_DECK_SIZE,
            opponent_name=names[1 - index],
        )
        await game_flow._send_to_player(
            session, index, content=_standard_intro(session), view=view,
        )

    await session.wait_for_both_players(timeout=None)

    return session.pending_actions.get(0), session.pending_actions.get(1)


async def run_tcg_draft_phase(
    game_flow: 'GameFlow', session: 'GameSession',
) -> tuple[list['Card'], list['Card'], list['Card'], list['Card']]:
    """Run the TCG draft for both players: ``(main_0, side_0, main_1, side_1)``. No timeout."""
    from zutomayo.ui.draft_views import DraftPackSelectionView

    session.clear_pending()
    names = game_flow._player_names(session)

    for index in range(2):
        view = DraftPackSelectionView(
            session=session,
            player_index=index,
            num_boxes=session.draft_boxes,
            mode='tcg',
            target_count=TCG_DRAFT_PICK_COUNT,
            opponent_name=names[1 - index],
        )
        await game_flow._send_to_player(
            session, index, content=_tcg_intro(session), view=view,
        )

    await session.wait_for_both_players(timeout=None)

    action_0 = session.pending_actions.get(0)
    action_1 = session.pending_actions.get(1)
    return action_0['deck'], action_0['side_deck'], action_1['deck'], action_1['side_deck']
