"""Draft legality. The draft is the first 40 plies: alternating picks, 20 each, from a
pool that never depletes, at most MAX_COPIES per card. The night player picks first.
"""

from __future__ import annotations

from .cards import NUM_CARDS

DECK_SIZE = 20
MAX_COPIES = 2


def legal_picks(deck_defs: list[int]) -> list[int]:
    counts = {}
    for def_index in deck_defs:
        counts[def_index] = counts.get(def_index, 0) + 1
    return [d for d in range(NUM_CARDS) if counts.get(d, 0) < MAX_COPIES]


def validate_deck(deck_defs: list[int]) -> None:
    if len(deck_defs) != DECK_SIZE:
        raise ValueError(f"deck must have exactly {DECK_SIZE} cards, got {len(deck_defs)}")
    counts = {}
    for def_index in deck_defs:
        if not 0 <= def_index < NUM_CARDS:
            raise ValueError(f"invalid card definition index {def_index}")
        counts[def_index] = counts.get(def_index, 0) + 1
        if counts[def_index] > MAX_COPIES:
            raise ValueError(f"more than {MAX_COPIES} copies of definition {def_index}")
