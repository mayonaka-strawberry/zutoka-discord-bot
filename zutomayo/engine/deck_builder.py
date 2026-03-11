import random
from collections import Counter
from zutomayo.enums.zone import Zone
from zutomayo.models.card import Card
from zutomayo.models.card_instance import CardInstance


def build_deck_from_cards(selected_cards: list[Card], owner: str) -> list[CardInstance]:
    """
    Convert a list of selected Card objects into CardInstance objects for gameplay.

    Args:
        selected_cards: Exactly 20 Card objects (may contain duplicates, max 2 of any).
        owner: The player's name/ID string.

    Returns:
        list[CardInstance] with zone=Zone.DECK.

    Raises:
        ValueError: If deck does not meet requirements.
    """
    if len(selected_cards) != 20:
        raise ValueError(f'Deck must contain exactly 20 cards, got {len(selected_cards)}')

    counts = Counter((card.pack, card.id) for card in selected_cards)
    for card_key, count in counts.items():
        if count > 2:
            raise ValueError(f'Card {card_key} appears {count} times (max 2)')

    return [
        CardInstance(card=card, owner=owner, zone=Zone.DECK)
        for card in selected_cards
    ]


def build_random_deck(all_cards: list[Card], owner: str) -> list[CardInstance]:
    """Build a random 20-card deck with max 2 copies per card."""
    pool = all_cards * 2
    random.shuffle(pool)
    selected: list[Card] = []
    counts: dict[tuple[int, int], int] = {}
    for card in pool:
        key = (card.pack, card.id)
        if counts.get(key, 0) < 2 and len(selected) < 20:
            selected.append(card)
            counts[key] = counts.get(key, 0) + 1
    return build_deck_from_cards(selected, owner)
