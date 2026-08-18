"""Solo-opponent agents and shared agent utilities for engine_alpha matches."""

from __future__ import annotations

import importlib
import random
from typing import Any

BOT_NAME = 'メカうにぐり'

SOLO_OPPONENT_ALPHA_ZERO = 'alphazero'
SOLO_OPPONENT_PPO = 'ppo'

SOLO_OPPONENT_MODULES = {
    SOLO_OPPONENT_ALPHA_ZERO: 'alpha_zero.inference',
    SOLO_OPPONENT_PPO: 'ppo_transformer.inference',
}

# Players only ever see the letter. Which stack sits behind each letter is an
# implementation detail and must not reach a Discord surface.
SOLO_OPPONENT_LABELS = {
    SOLO_OPPONENT_ALPHA_ZERO: 'A',
    SOLO_OPPONENT_PPO: 'B',
}


def solo_opponent_label(opponent: str) -> str:
    """The player-facing letter for a solo opponent identifier. Unknown values
    pass through unchanged, so legacy 'normal'/'easy' rows still read sensibly."""
    return SOLO_OPPONENT_LABELS.get(opponent, opponent)


def available_solo_opponents() -> list[str]:
    """Solo opponent identifiers whose inference module reports a usable
    trained checkpoint. Empty until a model has been trained and deployed."""
    available = []
    for opponent_name, module_name in SOLO_OPPONENT_MODULES.items():
        try:
            module = importlib.import_module(module_name)
            if module.find_checkpoint() is not None:
                available.append(opponent_name)
        except Exception:
            continue
    return available


def load_random_fallback_deck(card_index: dict[tuple[int, int], Any]) -> list[Any]:
    """A random pre-built deck as Card objects, used when a player never
    finishes deck building. Prefers the generated bot deck pool, falling back
    to the tracked default decks."""
    import json

    from zutomayo.data.deck_storage import (
        DEFAULT_DECKS_FILE,
        load_default_decks,
        resolve_deck_cards,
    )

    bot_decks_file = DEFAULT_DECKS_FILE.with_name('bot_decks.json')
    deck_entries: list[dict] = []
    if bot_decks_file.exists():
        with open(bot_decks_file, 'r', encoding='utf-8') as file_handle:
            deck_entries = json.load(file_handle).get('decks', [])
    if not deck_entries:
        deck_entries = load_default_decks()
    if not deck_entries:
        raise ValueError('No fallback decks available (bot_decks.json and default_decks.json empty).')
    chosen = random.choice(deck_entries)
    return resolve_deck_cards(chosen, card_index)
