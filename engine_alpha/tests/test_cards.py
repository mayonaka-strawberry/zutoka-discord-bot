"""M0 gate: card database integrity."""

from collections import Counter

from engine_alpha import cards
from engine_alpha.rng import shuffled, random_below


def test_card_count_and_types():
    assert cards.NUM_CARDS == 422
    type_counts = Counter(d.card_type for d in cards.CARD_DB)
    assert type_counts[cards.TYPE_CHARACTER] == 242
    assert type_counts[cards.TYPE_ENCHANT] == 153
    assert type_counts[cards.TYPE_AREA_ENCHANT] == 27


def test_effect_vocabulary():
    assert cards.NUM_EFFECTS == 250
    with_effect = [d for d in cards.CARD_DB if d.effect_index != cards.NO_EFFECT]
    assert len(with_effect) == 250
    # Effects are unique per card, so the reverse map is total.
    assert len(cards.EFFECT_TO_CARD) == 250
    # Effect id string matches the carrying card's own key.
    for d in with_effect:
        assert d.effect_id == d.key


def test_field_ranges():
    for d in cards.CARD_DB:
        assert 0 <= d.clock <= cards.MAX_CLOCK
        assert 0 <= d.power_cost <= cards.MAX_POWER_COST
        assert 0 <= d.attack_day <= cards.MAX_ATTACK
        assert 0 <= d.attack_night <= cards.MAX_ATTACK
        assert 0 <= d.send_to_power <= cards.MAX_SEND_TO_POWER
        assert 0 <= d.attribute < len(cards.ATTRIBUTE_NAMES)
        assert 0 <= d.card_type < len(cards.CARD_TYPE_NAMES)
        assert 0 <= d.song < cards.NUM_SONGS
        assert 0 <= d.rarity < len(cards.RARITY_NAMES)


def test_index_maps_round_trip():
    for d in cards.CARD_DB:
        assert cards.CARD_INDEX[(d.pack, d.number)] == d.index
        assert cards.KEY_TO_INDEX[d.key] == d.index
    assert cards.KEY_TO_INDEX["03-045"] == cards.CARD_INDEX[(3, 45)]


def test_flat_arrays_match_definitions():
    for d in cards.CARD_DB:
        assert cards.CLOCK_T[d.index] == d.clock
        assert cards.ATK_DAY_T[d.index] == d.attack_day
        assert cards.ATK_NIGHT_T[d.index] == d.attack_night
        assert cards.POWER_COST_T[d.index] == d.power_cost
        assert cards.SEND_TO_POWER_T[d.index] == d.send_to_power
        assert cards.EFFECT_T[d.index] == d.effect_index


def test_rng_determinism_and_permutation():
    items = list(range(20))
    a = shuffled(items, key=12345, counter=0)
    b = shuffled(items, key=12345, counter=0)
    assert a == b
    assert sorted(a) == items
    assert shuffled(items, key=12345, counter=1) != a  # different event, different order
    assert shuffled(items, key=54321, counter=0) != a  # different key, different order
    assert items == list(range(20))  # input not mutated

    values = {random_below(7, key=1, counter=c) for c in range(200)}
    assert values <= set(range(7))
    assert len(values) == 7
