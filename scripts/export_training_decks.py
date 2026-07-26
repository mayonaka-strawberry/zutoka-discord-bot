"""
Export every saved deck in the database as a training deck pool.

Usage:
    python scripts/export_training_decks.py --dry-run
    python scripts/export_training_decks.py
    python scripts/export_training_decks.py --include-defaults --min-users 2

The model stacks (alpha_zero/, ppo_transformer/) train against decks read from
this file, so they see the decks people actually build instead of uniformly
random ones. The output is a JSON snapshot rather than a live query: a training
machine does not need to reach the database, and the deck distribution stays
fixed for the length of a run. Re-run this script to refresh it.

Standard decks and TCG main decks are both exported. A TCG main deck is 20
cards whose copy limit is enforced across main plus side, so on its own it is
always a legal standard deck; side decks are ignored. Decks are deduplicated by
their sorted card list, and `user_count` records how many users saved each one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

from dotenv import load_dotenv

from engine_alpha.cards import KEY_TO_INDEX, NUM_CARDS
from engine_alpha.draft import validate_deck
from zutomayo.data import database, deck_repository
from zutomayo.data.deck_storage import load_default_decks

DEFAULT_OUTPUT_PATH = REPOSITORY_ROOT / 'data' / 'training_decks.json'

SOURCE_STANDARD = 'standard'
SOURCE_TCG_MAIN = 'tcg_main'
SOURCE_DEFAULT = 'default'

# (repository module attribute, deck entry field holding the 20-card list, source tag)
DECK_SOURCES = (
    ('STANDARD_DECK_REPOSITORY', 'cards', SOURCE_STANDARD),
    ('TCG_DECK_REPOSITORY', 'deck', SOURCE_TCG_MAIN),
)


def definition_indices_for_references(card_references: list[dict]) -> list[int]:
    """Convert stored {'pack', 'id'} references into engine definition indices."""
    return [
        KEY_TO_INDEX[f"{reference['pack']:02d}-{reference['id']:03d}"]
        for reference in card_references
    ]


def deck_signature(definition_indices: list[int]) -> str:
    """Order-independent identity of a deck, matching League.deck_signature."""
    return ','.join(str(index) for index in sorted(definition_indices))


class DeckCollector:
    """Accumulates decks by signature, counting distinct owners and sources."""

    def __init__(self) -> None:
        self.decks_by_signature: dict[str, dict] = {}
        self.skipped: list[str] = []

    def add(self, entry: dict, card_list_field: str, source: str) -> None:
        label = f"{source} deck '{entry.get('name', '?')}' " \
                f"(user {entry.get('user_id', 'n/a')})"
        try:
            definition_indices = definition_indices_for_references(entry[card_list_field])
        except (KeyError, TypeError) as error:
            self.skipped.append(f'{label}: unknown card reference {error}')
            return
        try:
            validate_deck(definition_indices)
        except ValueError as error:
            self.skipped.append(f'{label}: {error}')
            return

        signature = deck_signature(definition_indices)
        deck = self.decks_by_signature.get(signature)
        if deck is None:
            deck = {
                'signature': signature,
                'definitions': sorted(definition_indices),
                'sources': set(),
                'owners': set(),
            }
            self.decks_by_signature[signature] = deck
        deck['sources'].add(source)
        deck['owners'].add(entry.get('user_id'))

    def finalize(self, minimum_users: int) -> list[dict]:
        decks = []
        for deck in self.decks_by_signature.values():
            if len(deck['owners']) < minimum_users:
                continue
            decks.append({
                'signature': deck['signature'],
                'definitions': deck['definitions'],
                'sources': sorted(deck['sources']),
                'user_count': len(deck['owners']),
            })
        decks.sort(key=lambda deck: (-deck['user_count'], deck['signature']))
        return decks


async def collect_decks(include_defaults: bool) -> DeckCollector:
    collector = DeckCollector()
    for repository_name, card_list_field, source in DECK_SOURCES:
        repository = getattr(deck_repository, repository_name)
        for entry in await repository.list_all_decks():
            collector.add(entry, card_list_field, source)
    if include_defaults:
        for entry in load_default_decks():
            # Default decks have no owner; give each one a distinct placeholder
            # so they are not collapsed into a single owner by the dedup counter.
            collector.add({**entry, 'user_id': f"default:{entry.get('name')}"},
                          'cards', SOURCE_DEFAULT)
    return collector


async def export_training_decks(output_path: Path, include_defaults: bool,
                                minimum_users: int, dry_run: bool) -> None:
    await database.initialize_pool()
    try:
        collector = await collect_decks(include_defaults)
    finally:
        await database.close_pool()

    decks = collector.finalize(minimum_users)
    for message in collector.skipped:
        print(f'skipped {message}')
    print(f'{len(collector.decks_by_signature)} distinct deck(s) found, '
          f'{len(decks)} kept (min-users {minimum_users}), '
          f'{len(collector.skipped)} skipped.')

    if dry_run:
        print('dry run: nothing written.')
        return

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'card_count': NUM_CARDS,
        'decks': decks,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + '.tmp')
    with open(temporary_path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=1)
    temporary_path.replace(output_path)
    print(f'wrote {len(decks)} deck(s) to {output_path}')


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description='Export all saved decks as a training deck pool.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT_PATH,
                        help=f'Destination JSON file (default {DEFAULT_OUTPUT_PATH}).')
    parser.add_argument('--include-defaults', action='store_true',
                        help='Also export the pre-built decks in default_decks.json.')
    parser.add_argument('--min-users', type=int, default=1,
                        help='Keep only decks saved by at least this many users.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would be exported without writing the file.')
    arguments = parser.parse_args()
    asyncio.run(export_training_decks(
        arguments.output, arguments.include_defaults,
        arguments.min_users, arguments.dry_run))


if __name__ == '__main__':
    main()
