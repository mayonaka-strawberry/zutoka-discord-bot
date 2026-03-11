import random
from zutomayo.enums.rarity import Rarity
from zutomayo.models.card import Card


# Slot definitions: each slot is a list of (rarity, weight) tuples.
_SLOT_WEIGHTS: list[list[tuple[Rarity, float]]] = [
    [(Rarity.UR, 0.2), (Rarity.SR, 0.77), (Rarity.SE, 0.03)],
    [(Rarity.R, 1.0)],
    [(Rarity.R, 0.45), (Rarity.N, 0.55)],
    [(Rarity.N, 1.0)],
    [(Rarity.N, 1.0)],
]


def _pick_rarity(weights: list[tuple[Rarity, float]]) -> list[Rarity]:
    """Return rarities ordered by weighted random selection."""
    rarities = [r for r, _ in weights]
    w = [w for _, w in weights]
    return random.choices(rarities, weights=w, k=1)


def draw_gacha(pack: int, all_cards: list[Card], *, force_slot1_rarity: Rarity | None = None, excluded_ids: set[tuple[int, int]] | None = None) -> list[Card]:
    """
    Draw 5 cards from *pack* following the gacha slot rules.

    Returns a list of 5 unique ``Card`` objects in slot order.
    """
    # Group pack cards by rarity
    pools: dict[Rarity, list[Card]] = {}
    for card in all_cards:
        if card.pack == pack:
            pools.setdefault(card.rarity, []).append(card)

    # If the pack has no SE cards, add SE probability to SR
    adjusted_slots: list[list[tuple[Rarity, float]]] = []
    for slot in _SLOT_WEIGHTS:
        if any(r == Rarity.SE for r, _ in slot) and Rarity.SE not in pools:
            sr_bonus = sum(w for r, w in slot if r == Rarity.SE)
            adjusted = [(r, w + sr_bonus) if r == Rarity.SR else (r, w)
                        for r, w in slot if r != Rarity.SE]
            adjusted_slots.append(adjusted)
        else:
            adjusted_slots.append(slot)

    drawn: list[Card] = []
    drawn_ids: set[tuple[int, int]] = set(excluded_ids) if excluded_ids else set()

    for slot_idx, slot_weights in enumerate(adjusted_slots):
        # Determine rarity for this slot
        if slot_idx == 0 and force_slot1_rarity is not None:
            rarity = force_slot1_rarity
        else:
            rarity = _pick_rarity(slot_weights)[0]
        available = [c for c in pools.get(rarity, []) if (c.pack, c.id) not in drawn_ids]

        # Fallback: if chosen rarity pool is exhausted, try other rarities in this slot
        if not available:
            for r, _ in slot_weights:
                if r != rarity:
                    available = [c for c in pools.get(r, []) if (c.pack, c.id) not in drawn_ids]
                    if available:
                        break

        card = random.choice(available)
        drawn.append(card)
        drawn_ids.add((card.pack, card.id))

    return drawn


def draw_gachabox(pack: int, all_cards: list[Card]) -> list[Card]:
    """
    Draw 10 packs of 5 cards (50 total) with guaranteed rarity distribution.

    Exactly 2 of the 10 packs contain a UR card in slot 1.
    At most 1 pack contains an SE card in slot 1.
    UR and SR cards are unique across the entire box (no repeats).
    Returns a flat list of 50 ``Card`` objects (pack 1 cards first, etc.).
    """
    num_packs = 10
    pack_indices = list(range(num_packs))

    # Exactly 2 packs get UR in slot 1
    ur_indices = set(random.sample(pack_indices, 2))

    # At most 1 pack gets SE in slot 1
    remaining = [i for i in pack_indices if i not in ur_indices]
    se_index: int | None = None
    has_se = any(c.rarity == Rarity.SE and c.pack == pack for c in all_cards)
    if has_se and random.random() < 0.26:
        se_index = random.choice(remaining)

    all_drawn: list[Card] = []
    excluded_ids: set[tuple[int, int]] = set()
    for i in range(num_packs):
        if i in ur_indices:
            forced = Rarity.UR
        elif i == se_index:
            forced = Rarity.SE
        else:
            forced = Rarity.SR
        drawn = draw_gacha(pack, all_cards, force_slot1_rarity=forced, excluded_ids=excluded_ids)
        for card in drawn:
            if card.rarity in (Rarity.UR, Rarity.SR):
                excluded_ids.add((card.pack, card.id))
        all_drawn.extend(drawn)

    return all_drawn
