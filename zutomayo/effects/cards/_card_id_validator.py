"""Shared validator for card ID input (XX-XXX format) used by guessing effects."""

from __future__ import annotations
import re


_TOKEN_RE = re.compile(r'^\d{2}-\d{3}$')

_card_index: dict[tuple[int, int], object] | None = None


def _get_card_index() -> dict[tuple[int, int], object]:
    global _card_index
    if _card_index is None:
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_validator import build_card_index
        _card_index = build_card_index(load_cards())
    return _card_index


def validate_card_id(value: str) -> str | None:
    """Return an error message if *value* is not a valid card ID, else None."""
    value = value.strip()
    if not _TOKEN_RE.match(value):
        return f'Invalid format: **{value}**. Please use XX-XXX (e.g. 03-047).'
    pack, card_id = int(value[:2]), int(value[3:])
    if (pack, card_id) not in _get_card_index():
        return f'Card **{value}** does not exist. Please enter a valid card ID.'
    return None
