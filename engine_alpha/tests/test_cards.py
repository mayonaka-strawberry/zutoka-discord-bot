"""Card database integrity, plus RNG determinism."""

import unicodedata
from collections import Counter, defaultdict

from engine_alpha import cards
from engine_alpha.rng import shuffled, random_below


def test_card_count_and_types():
    assert cards.NUM_CARDS == 425
    type_counts = Counter(d.card_type for d in cards.CARD_DB)
    assert type_counts[cards.TYPE_CHARACTER] == 243
    assert type_counts[cards.TYPE_ENCHANT] == 155
    assert type_counts[cards.TYPE_AREA_ENCHANT] == 27


def test_effect_vocabulary():
    assert cards.NUM_EFFECTS == 253
    with_effect = [d for d in cards.CARD_DB if d.effect_index != cards.NO_EFFECT]
    assert len(with_effect) == 253
    # Effects are unique per card, so the reverse map is total.
    assert len(cards.EFFECT_TO_CARD) == 253
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


def partition_by(name_key) -> set[frozenset[int]]:
    """The definitions grouped by name_key(definition)."""
    groups = defaultdict(set)
    for definition in cards.CARD_DB:
        groups[name_key(definition)].add(definition.index)
    return {frozenset(group) for group in groups.values()}


def collapsed_nfkc(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def test_card_names_are_full_printed_names():
    """Q&A No.50: a card name includes the part in parentheses, so printings share a
    name only when name_jp is identical. Each name maps to its lowest definition index."""
    assert cards.NUM_CARD_NAMES == 337
    by_name_jp = partition_by(lambda definition: definition.name_jp)
    assert partition_by(lambda definition: cards.NAME_REPRESENTATIVE_T[definition.index]) == by_name_jp
    assert len(by_name_jp) == cards.NUM_CARD_NAMES
    for group in by_name_jp:
        for index in group:
            assert cards.NAME_REPRESENTATIVE_T[index] == min(group)
        assert len({cards.CARD_TYPE_T[index] for index in group}) == 1, "one card type per name"


def test_english_names_group_like_japanese_names():
    assert partition_by(lambda definition: definition.name) == \
        partition_by(lambda definition: definition.name_jp)


def test_name_data_has_no_width_or_spacing_variants():
    """Data lint: names that differ only in character width or spacing would split one
    name in two, so NFKC with collapsed whitespace must not merge any groups."""
    by_name_jp = partition_by(lambda definition: definition.name_jp)
    assert partition_by(lambda definition: collapsed_nfkc(definition.name_jp)) == by_name_jp
    assert partition_by(lambda definition: collapsed_nfkc(definition.name).casefold()) == by_name_jp


def test_pinned_card_name_groups():
    for keys in (("01-022", "02-079", "03-047"),
                 ("01-047", "02-016", "04-087"),
                 ("01-071", "03-016", "04-015")):
        representative = cards.KEY_TO_INDEX[keys[0]]
        group = {definition.key for definition in cards.CARD_DB
                 if cards.NAME_REPRESENTATIVE_T[definition.index] == representative}
        assert group == set(keys)
        assert cards.same_card_name(cards.KEY_TO_INDEX[keys[1]], cards.KEY_TO_INDEX[keys[2]])
    assert cards.CARD_DB[cards.KEY_TO_INDEX["03-047"]].name_jp == "にらちゃん（ヒューマノイド）"
    assert not cards.same_card_name(cards.KEY_TO_INDEX["04-078"], cards.KEY_TO_INDEX["04-088"])
    assert not cards.same_card_name(cards.KEY_TO_INDEX["01-001"], cards.KEY_TO_INDEX["04-001"])
    assert not cards.same_card_name(cards.KEY_TO_INDEX["01-001"], cards.KEY_TO_INDEX["01-047"])


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
