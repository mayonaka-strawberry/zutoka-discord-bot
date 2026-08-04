"""Small card-catalog helpers shared by tests."""

from __future__ import annotations

from zutomayo.data.card_loader import load_cards


def card_by_identity(identity: str):
    """Find a catalog Card by its 'PP-III' identity, for example '01-013'."""
    pack_text, _, number_text = identity.partition('-')
    pack, card_id = int(pack_text), int(number_text)
    for card in load_cards():
        if card.pack == pack and card.id == card_id:
            return card
    raise KeyError(identity)
