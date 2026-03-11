"""
Deck list text validation and parsing.

Accepts a space-separated string of 20 card tokens in XX-YYY format
(zero-padded pack and card id) and returns the resolved Card objects
or a list of human-readable error messages.
"""

from __future__ import annotations
import re
from collections import Counter
from zutomayo.models.card import Card


# Strict per-token pattern: exactly 2 digits, hyphen, exactly 3 digits
_TOKEN_RE = re.compile(r'^\d{2}-\d{3}$')

DECK_SIZE = 20
MAX_COPIES = 2


def build_card_index(all_cards: list[Card]) -> dict[tuple[int, int], Card]:
    """Build a lookup dict from (pack, id) -> Card for O(1) validation."""
    return {(card.pack, card.id): card for card in all_cards}


def parse_deck_input(
    raw_input: str,
    card_index: dict[tuple[int, int], Card],
) -> tuple[list[Card] | None, list[str]]:
    """
    Parse and validate a deck list string.

    Args:
        raw_input: The raw text from the user's modal input.
        card_index: Pre-built (pack, id) -> Card lookup from build_card_index().

    Returns:
        A tuple of (cards, errors):
        - cards is a list[Card] of exactly 20 cards if valid, or None if invalid
        - errors is a list of human-readable error strings (empty if valid)
    """
    errors: list[str] = []

    cleaned = raw_input.strip()
    if not cleaned:
        return None, ['Input is empty. Please enter 20 cards in XX-YYY format.']

    tokens = cleaned.split()

    if len(tokens) != DECK_SIZE:
        errors.append(f'Expected exactly {DECK_SIZE} cards, got {len(tokens)}.')

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
        errors.append('Invalid format:\n' + '\n'.join(bad_format))

    if not_found:
        errors.append('Card not found:\n' + '\n'.join(not_found))

    if resolved_cards:
        counts = Counter((card.pack, card.id) for card in resolved_cards)
        over_limit = [
            f'  {k[0]:02d}-{k[1]:03d} appears {v} times (max {MAX_COPIES})'
            for k, v in counts.items()
            if v > MAX_COPIES
        ]
        if over_limit:
            errors.append('Too many copies:\n' + '\n'.join(over_limit))

    if errors:
        return None, errors

    return resolved_cards, []
