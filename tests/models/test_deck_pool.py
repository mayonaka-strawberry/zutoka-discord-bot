"""Training deck pool: the export script, the loader, and the sampler.

The stacks train against decks players actually saved, so this covers the whole
path — database rows to definition indices to the deck a self-play game starts
with. All of it runs on tracked files, so no importorskip is needed here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from engine_alpha.cards import CARD_DB, NUM_CARDS  # noqa: E402
from engine_alpha.draft import DECK_SIZE, MAX_COPIES  # noqa: E402
from model_common.deck_pool import (  # noqa: E402
    DEFAULT_DISTINCT_COUNT_WEIGHTS,
    DISTINCT_DEFINITION_COUNTS,
    MINIMUM_DISTINCT_DEFINITIONS,
    DeckSampler,
    derive_distinct_count_weights,
    load_deck_pool,
    random_legal_deck,
)


def _load_script_module(script_name: str):
    script_path = REPOSITORY_ROOT / 'scripts' / f'{script_name}.py'
    specification = importlib.util.spec_from_file_location(script_name, script_path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _legal_deck(first_definition: int = 0) -> list[int]:
    """A 20-card deck of consecutive definitions, one copy each."""
    return [(first_definition + offset) % NUM_CARDS for offset in range(DECK_SIZE)]


def _card_references(definition_indices: list[int]) -> list[dict]:
    return [{'pack': CARD_DB[index].pack, 'id': CARD_DB[index].number}
            for index in definition_indices]


def _write_pool(path: Path, decks: list[list[int]]) -> None:
    path.write_text(json.dumps({
        'generated_at': '2026-07-25T00:00:00+00:00',
        'card_count': NUM_CARDS,
        'decks': [{'signature': ','.join(str(index) for index in sorted(deck)),
                   'definitions': deck, 'sources': ['standard'], 'user_count': 1}
                  for deck in decks],
    }), encoding='utf-8')


# -- loader ------------------------------------------------------------------

def test_load_deck_pool_returns_empty_when_file_is_missing(tmp_path):
    assert load_deck_pool(tmp_path / 'absent.json') == []


def test_load_deck_pool_reads_definitions(tmp_path):
    pool_path = tmp_path / 'training_decks.json'
    _write_pool(pool_path, [_legal_deck(0), _legal_deck(50)])

    pool = load_deck_pool(pool_path)

    assert pool == [_legal_deck(0), _legal_deck(50)]


def test_load_deck_pool_drops_illegal_decks(tmp_path):
    """A stale export must not abort a run, just lose the offending decks."""
    pool_path = tmp_path / 'training_decks.json'
    _write_pool(pool_path, [
        _legal_deck(0),
        _legal_deck(0)[:19],                 # too short
        [7] * DECK_SIZE,                     # over the copy limit
        [NUM_CARDS] + _legal_deck(0)[1:],    # definition index out of range
    ])

    assert load_deck_pool(pool_path) == [_legal_deck(0)]


# -- random deck generator ---------------------------------------------------

def _generated_distinct_counts(draws: int = 4000, weights=None,
                               seed: int = 11) -> Counter:
    rng = random.Random(seed)
    return Counter(len(set(random_legal_deck(rng, weights))) for _ in range(draws))


def test_random_legal_deck_is_legal():
    rng = random.Random(11)
    for _ in range(300):
        deck = random_legal_deck(rng)
        counts = Counter(deck)
        assert len(deck) == DECK_SIZE
        assert max(counts.values()) <= MAX_COPIES
        assert all(0 <= definition < NUM_CARDS for definition in deck)


def test_random_legal_deck_skews_toward_maximum_copies():
    """Real decks run two copies of most cards; generated ones must too.

    A uniform draw over the legal distinct-card counts would sit at a mean of
    15 and make full-pairs decks a 1-in-11 event, which is the regression this
    guards against.
    """
    observed = _generated_distinct_counts()
    total = sum(observed.values())
    mean_distinct = sum(count * n for count, n in observed.items()) / total

    assert observed.most_common(1)[0][0] == MINIMUM_DISTINCT_DEFINITIONS
    assert mean_distinct < 13.5
    assert observed[MINIMUM_DISTINCT_DEFINITIONS] / total > 0.25


def test_random_legal_deck_can_produce_every_legal_structure():
    """Smoothing keeps counts with no observation in the export reachable."""
    observed = _generated_distinct_counts(draws=20000)

    assert set(observed) == set(DISTINCT_DEFINITION_COUNTS)


def test_generated_decks_follow_the_pool_distribution():
    """A sampler's generated decks borrow the copy structure of its pool."""
    singleton_heavy = [_legal_deck(index * 20) for index in range(20)]
    weights = derive_distinct_count_weights(singleton_heavy)

    observed = _generated_distinct_counts(weights=weights)

    # Every deck in that pool holds 20 distinct cards, so generation should
    # concentrate there rather than at the max-copies end the default prefers.
    assert observed.most_common(1)[0][0] == DECK_SIZE
    assert observed[DECK_SIZE] > observed[MINIMUM_DISTINCT_DEFINITIONS]


# -- distinct-count weights --------------------------------------------------

def test_derive_weights_follows_the_pool():
    pool = [_legal_deck(0)[:10] * 2,      # 10 distinct, 2 copies each
            _legal_deck(40)[:10] * 2,
            _legal_deck(80)]              # 20 distinct, 1 copy each
    weights = derive_distinct_count_weights(pool)

    by_count = dict(zip(DISTINCT_DEFINITION_COUNTS, weights))

    assert by_count[MINIMUM_DISTINCT_DEFINITIONS] == 3.0   # 2 observed + 1
    assert by_count[DECK_SIZE] == 2.0                      # 1 observed + 1
    assert by_count[15] == 1.0                             # unobserved, smoothed


def test_derive_weights_falls_back_to_the_measured_default():
    weights = derive_distinct_count_weights([])

    assert len(weights) == len(DEFAULT_DISTINCT_COUNT_WEIGHTS)
    assert weights == tuple(float(weight + 1)
                            for weight in DEFAULT_DISTINCT_COUNT_WEIGHTS)
    assert all(weight > 0 for weight in weights)


# -- sampler -----------------------------------------------------------------

def test_deck_sampler_always_uses_pool_at_probability_one():
    pool = [_legal_deck(0), _legal_deck(100)]
    sampler = DeckSampler(pool, probability_user_deck=1.0)
    rng = random.Random(5)

    for _ in range(50):
        assert sampler.sample(rng) in pool


def test_deck_sampler_never_uses_pool_at_probability_zero():
    pool = [_legal_deck(0)]
    sampler = DeckSampler(pool, probability_user_deck=0.0)
    rng = random.Random(5)

    drawn = [sampler.sample(rng) for _ in range(50)]

    assert all(deck != pool[0] for deck in drawn)


def test_deck_sampler_falls_back_to_random_without_a_pool():
    sampler = DeckSampler([], probability_user_deck=1.0)
    rng = random.Random(5)

    assert sampler.probability_user_deck == 0.0
    assert len(sampler.sample(rng)) == DECK_SIZE


def test_deck_sampler_returns_a_copy_so_callers_cannot_corrupt_the_pool():
    pool = [_legal_deck(0)]
    sampler = DeckSampler(pool, probability_user_deck=1.0)

    drawn = sampler.sample(random.Random(1))
    drawn[0] = -1

    assert pool[0] == _legal_deck(0)


# -- export script -----------------------------------------------------------

@pytest.fixture
def export_module():
    return _load_script_module('export_training_decks')


def test_export_collects_standard_and_tcg_main_decks(export_module,
                                                     install_in_memory_backends):
    standard = install_in_memory_backends['decks']
    tcg = install_in_memory_backends['decks_tcg']
    standard.decks_by_user[1] = {'aggro': {
        'name': 'aggro', 'cards': _card_references(_legal_deck(0))}}
    tcg.decks_by_user[2] = {'control': {
        'name': 'control',
        'deck': _card_references(_legal_deck(100)),
        'side_deck': _card_references(_legal_deck(200))[:8],
    }}

    collector = asyncio.run(export_module.collect_decks(include_defaults=False))
    decks = collector.finalize(minimum_users=1)

    assert collector.skipped == []
    assert {tuple(deck['definitions']) for deck in decks} == {
        tuple(sorted(_legal_deck(0))), tuple(sorted(_legal_deck(100)))}
    assert {source for deck in decks for source in deck['sources']} == {
        'standard', 'tcg_main'}


def test_export_deduplicates_identical_decks_and_counts_owners(
        export_module, install_in_memory_backends):
    standard = install_in_memory_backends['decks']
    shared = _card_references(_legal_deck(0))
    standard.decks_by_user[1] = {'mine': {'name': 'mine', 'cards': shared}}
    standard.decks_by_user[2] = {'copied': {'name': 'copied', 'cards': list(reversed(shared))}}
    standard.decks_by_user[3] = {'other': {
        'name': 'other', 'cards': _card_references(_legal_deck(50))}}

    collector = asyncio.run(export_module.collect_decks(include_defaults=False))

    assert len(collector.finalize(minimum_users=1)) == 2
    popular = collector.finalize(minimum_users=2)
    assert len(popular) == 1
    assert popular[0]['user_count'] == 2
    assert popular[0]['definitions'] == sorted(_legal_deck(0))


def test_export_skips_illegal_and_unknown_decks(export_module,
                                                install_in_memory_backends):
    standard = install_in_memory_backends['decks']
    standard.decks_by_user[1] = {
        'good': {'name': 'good', 'cards': _card_references(_legal_deck(0))},
        'short': {'name': 'short', 'cards': _card_references(_legal_deck(0))[:19]},
        'unknown': {'name': 'unknown',
                    'cards': [{'pack': 99, 'id': 999}] * DECK_SIZE},
    }

    collector = asyncio.run(export_module.collect_decks(include_defaults=False))
    decks = collector.finalize(minimum_users=1)

    assert len(decks) == 1
    assert len(collector.skipped) == 2
    assert any('short' in message for message in collector.skipped)
    assert any('unknown' in message for message in collector.skipped)


def test_exported_file_loads_back_as_a_deck_pool(export_module, tmp_path,
                                                 install_in_memory_backends):
    """The script and the loader must agree on the file format."""
    standard = install_in_memory_backends['decks']
    standard.decks_by_user[1] = {'aggro': {
        'name': 'aggro', 'cards': _card_references(_legal_deck(0))}}
    output_path = tmp_path / 'training_decks.json'

    collector = asyncio.run(export_module.collect_decks(include_defaults=False))
    output_path.write_text(json.dumps({
        'generated_at': '2026-07-25T00:00:00+00:00',
        'card_count': NUM_CARDS,
        'decks': collector.finalize(minimum_users=1),
    }), encoding='utf-8')

    assert load_deck_pool(output_path) == [sorted(_legal_deck(0))]
