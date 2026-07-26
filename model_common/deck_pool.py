"""Shared deck source for the training stacks.

Both stacks play a large fraction of their games with fixed decks, and what
those decks look like decides what the model gets good at. The pool is the set
of decks real players have saved, exported by
`scripts/export_training_decks.py`; `DeckSampler` mixes it with freshly
generated random decks so cards nobody plays still receive gradient. Those
generated decks range over the whole card pool but borrow the pool's
distribution of copy counts, so they are unfamiliar in content without being
unrealistic in shape.

The pool file is optional: on a machine without an export, `load_deck_pool`
returns an empty list and the sampler falls back to random decks, so training
still runs.

Decks are stored on disk as `{'pack', 'id'}` card references — the same shape
decks take everywhere else in the repository — and converted to engine
definition indices at load. Storing the indices themselves would not survive a
catalog change: an index is a position in a list sorted by (pack, id), so adding
one card to an early pack shifts every later index and a stale export would
resolve to different cards while still passing `validate_deck`.
"""

from __future__ import annotations

import json
import random
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path

from engine_alpha.cards import CARD_DB, KEY_TO_INDEX, NUM_CARDS
from engine_alpha.draft import DECK_SIZE, MAX_COPIES, validate_deck

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECK_POOL_PATH = REPOSITORY_ROOT / 'data' / 'training_decks.json'

# Namespace for deck guids. Frozen forever: changing it renames every deck in
# every exported file and breaks any reference anyone has recorded.
DECK_GUID_NAMESPACE = uuid.UUID('26aa4c7c-31f6-415f-b76c-63e45c267d91')

# A deck of DECK_SIZE cards with at most MAX_COPIES of each definition needs at
# least this many distinct definitions.
MINIMUM_DISTINCT_DEFINITIONS = -(-DECK_SIZE // MAX_COPIES)
DISTINCT_DEFINITION_COUNTS = range(MINIMUM_DISTINCT_DEFINITIONS, DECK_SIZE + 1)

# How many distinct definitions a deck holds, counted over the 110 decks
# exported on 2026-07-25; index 0 is MINIMUM_DISTINCT_DEFINITIONS. Players run
# two copies of what they play, so the mass sits at the low end: 37% of decks
# are a full 10 pairs and the mean is 12.2 distinct cards, not the 15 a uniform
# draw would give. Used only when no export is available — a loaded pool
# supplies its own weights through derive_distinct_count_weights, so the
# generated decks track the live meta rather than this snapshot.
DEFAULT_DISTINCT_COUNT_WEIGHTS = (41, 19, 17, 12, 5, 2, 1, 2, 0, 3, 8)


def card_references(definition_indices: list[int]) -> list[dict]:
    """Definition indices as the {'pack', 'id'} shape decks are stored in."""
    return [{'pack': CARD_DB[index].pack, 'id': CARD_DB[index].number}
            for index in definition_indices]


def definition_indices_for_references(references: list[dict]) -> list[int]:
    """Stored {'pack', 'id'} references back to engine definition indices.

    Raises KeyError for a card the current catalog does not have, which is the
    point of storing references: a deck naming a removed card fails loudly
    instead of silently resolving to whichever card now holds that index.
    """
    return [
        KEY_TO_INDEX[f"{reference['pack']:02d}-{reference['id']:03d}"]
        for reference in references
    ]


def deck_guid(definition_indices: list[int]) -> str:
    """Stable identity of a deck, derived from its cards.

    A uuid5 over the sorted card keys, so the same 20 cards always produce the
    same guid: across re-exports, across machines, and for any deck held in
    memory as definition indices. That is what lets an exported entry be matched
    back to a deck a training run drafted. Keys rather than indices, so a
    catalog change does not rename every deck.
    """
    keys = sorted(CARD_DB[index].key for index in definition_indices)
    return str(uuid.uuid5(DECK_GUID_NAMESPACE, ','.join(keys)))


def load_deck_pool(path: str | Path | None = None) -> list[list[int]]:
    """Decks from a training-deck export, as lists of definition indices.

    Returns [] when the file does not exist. Individual decks that name an
    unknown card or fail engine legality are dropped rather than aborting the
    run — the export may predate a catalog change.
    """
    resolved = Path(path) if path else DEFAULT_DECK_POOL_PATH
    if not resolved.exists():
        return []
    with open(resolved, encoding='utf-8') as handle:
        payload = json.load(handle)
    pool: list[list[int]] = []
    for deck in payload.get('decks', []):
        try:
            definitions = definition_indices_for_references(deck['cards'])
            validate_deck(definitions)
        except (KeyError, TypeError, ValueError):
            continue
        pool.append(definitions)
    return pool


def describe_deck_pool(pool: list[list[int]], path: str | Path | None = None) -> str:
    """One-line startup log so a missing or stale export is never silent."""
    resolved = Path(path) if path else DEFAULT_DECK_POOL_PATH
    if not pool:
        return f'deck pool: none found at {resolved}, using random decks only'
    return f'deck pool: {len(pool)} deck(s) from {resolved}'


def derive_distinct_count_weights(pool: list[list[int]]) -> tuple[float, ...]:
    """Add-one smoothed histogram of how many distinct definitions decks hold.

    Falls back to DEFAULT_DISTINCT_COUNT_WEIGHTS for an empty pool. The
    smoothing keeps every legal structure reachable: counts with no observation
    in the current export (18 distinct, at the time of writing) would otherwise
    have weight zero and never be generated at all.
    """
    observed = Counter(len(set(deck)) for deck in pool) if pool else Counter({
        count: weight for count, weight
        in zip(DISTINCT_DEFINITION_COUNTS, DEFAULT_DISTINCT_COUNT_WEIGHTS)})
    return tuple(float(observed.get(count, 0) + 1)
                 for count in DISTINCT_DEFINITION_COUNTS)


def random_legal_deck(rng: random.Random,
                      distinct_count_weights: tuple[float, ...] | None = None) -> list[int]:
    """A random legal deck whose copy structure resembles a real one.

    Draws the distinct-card count from `distinct_count_weights` first, then
    decides which of those definitions appear twice. The weights matter: real
    decks run two copies of most cards, so sampling cards independently (which
    would almost always yield 20 distinct definitions out of a 425-wide pool)
    or drawing the count uniformly both produce decks with a card economy that
    does not occur in play.
    """
    weights = distinct_count_weights or derive_distinct_count_weights([])
    distinct_count = rng.choices(DISTINCT_DEFINITION_COUNTS, weights=weights)[0]
    definitions = rng.sample(range(NUM_CARDS), distinct_count)
    doubled = rng.sample(definitions, DECK_SIZE - distinct_count)
    deck = definitions + doubled
    rng.shuffle(deck)
    return deck


class DeckSampler:
    """Draws a deck from the pool with probability_user_deck, else a random one.

    Stateless apart from the pool: the caller supplies the rng, so alpha_zero
    can keep deriving a game's decks from that game's seed while PPO can pass
    its single rollout rng.
    """

    def __init__(self, pool: list[list[int]], probability_user_deck: float) -> None:
        self.pool = pool
        self.probability_user_deck = probability_user_deck if pool else 0.0
        # Generated decks borrow the pool's copy structure, so the quarter of
        # games that do not use a real deck still teach a real card economy.
        self.distinct_count_weights = derive_distinct_count_weights(pool)

    def sample(self, rng: random.Random) -> list[int]:
        if self.pool and rng.random() < self.probability_user_deck:
            return list(rng.choice(self.pool))
        return random_legal_deck(rng, self.distinct_count_weights)


@lru_cache(maxsize=None)
def shared_deck_sampler(path: str | None,
                        probability_user_deck: float) -> DeckSampler:
    """Sampler for a (path, probability) pair, reading the pool once per process.

    Safe to share and to rebuild inside a spawned worker: the returned sampler
    holds no rng and no other mutable state.
    """
    return DeckSampler(load_deck_pool(path), probability_user_deck)
