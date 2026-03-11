"""
TCG deck list text validation and parsing.

Accepts two space-separated strings: one with 20 main deck card tokens
and one with 8 side deck card tokens, both in XX-YYY format.
Validates max 2 copies of any card across both decks combined.
"""

from __future__ import annotations
import re
from collections import Counter
from zutomayo.models.card import Card


_TOKEN_RE = re.compile(r'^\d{2}-\d{3}$')

TCG_DECK_SIZE = 20
TCG_SIDE_DECK_SIZE = 8
MAX_COPIES = 2


def _parse_tokens(
    raw_input: str,
    expected_count: int,
    label: str,
    card_index: dict[tuple[int, int], Card],
) -> tuple[list[Card], list[str]]:
    """Parse a space-separated card list string and return resolved cards + errors."""
    errors: list[str] = []
    cleaned = raw_input.strip()
    if not cleaned:
        return [], [f'{label} is empty. Please enter {expected_count} cards in XX-YYY format.']

    tokens = cleaned.split()

    if len(tokens) != expected_count:
        errors.append(f'{label}: Expected exactly {expected_count} cards, got {len(tokens)}.')

    resolved_cards: list[Card] = []
    bad_format: list[str] = []
    not_found: list[str] = []

    for i, token in enumerate(tokens):
        if not _TOKEN_RE.match(token):
            bad_format.append(f"  Position {i + 1}: '{token}' - must be XX-YYY format")
            continue

        pack_string, id_string = token.split('-')
        pack = int(pack_string)
        card_id = int(id_string)

        card = card_index.get((pack, card_id))
        if card is None:
            not_found.append(
                f"  Position {i + 1}: '{token}' - no card with pack={pack}, id={card_id}"
            )
        else:
            resolved_cards.append(card)

    if bad_format:
        errors.append(f'{label} invalid format:\n' + '\n'.join(bad_format))

    if not_found:
        errors.append(f'{label} card not found:\n' + '\n'.join(not_found))

    return resolved_cards, errors


def parse_tcg_deck_input(
    main_input: str,
    side_input: str,
    card_index: dict[tuple[int, int], Card],
) -> tuple[tuple[list[Card], list[Card]] | None, list[str]]:
    """
    Parse and validate a TCG deck list (main + side deck).

    Returns:
        A tuple of ((main_cards, side_cards), errors):
        - On success: ((list[Card] of 20, list[Card] of 8), [])
        - On failure: (None, list of error strings)
    """
    main_cards, main_errors = _parse_tokens(main_input, TCG_DECK_SIZE, 'Main deck', card_index)
    side_cards, side_errors = _parse_tokens(side_input, TCG_SIDE_DECK_SIZE, 'Side deck', card_index)

    errors = main_errors + side_errors

    # Check max copies across both decks combined
    all_resolved = main_cards + side_cards
    if all_resolved:
        counts = Counter((card.pack, card.id) for card in all_resolved)
        over_limit = [
            f'  {k[0]:02d}-{k[1]:03d} appears {v} times (max {MAX_COPIES})'
            for k, v in counts.items()
            if v > MAX_COPIES
        ]
        if over_limit:
            errors.append('Too many copies (across main + side deck):\n' + '\n'.join(over_limit))

    if errors:
        return None, errors

    return (main_cards, side_cards), []
