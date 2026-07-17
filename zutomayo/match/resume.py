"""
Rebuild and replay engine_alpha matches from their persisted records.

The core primitive: reconstruct the Game from the manifest (seed + decks),
put the broker in replay mode with the loaded decision log, and run the
normal driver with the transport muted. Deterministic replay reproduces the
exact state; when the log is exhausted the broker goes live and play
continues. The full startup/resume orchestration (session rebuilding,
Discord announcements) is wired by the match flow.
"""

from __future__ import annotations

import logging
from typing import Any

from zutomayo.match.persistence import definition_indices_for_card_keys

log = logging.getLogger(__name__)


def rebuild_game_from_manifest(manifest: dict[str, Any]) -> Any:
    """Reconstruct the engine game exactly as it was first created."""
    from engine_alpha.game import Game

    decks = (
        definition_indices_for_card_keys(manifest['deck_0']),
        definition_indices_for_card_keys(manifest['deck_1']),
    )
    return Game(seed=manifest['random_seed'], mode='fixed_decks', decks=decks)


async def load_replay_state(broker: Any, game_id: str) -> None:
    """Load the persisted decision log into a broker and enter replay mode."""
    from zutomayo.match.persistence import load_match_decision_log

    broker.replay_log = await load_match_decision_log(game_id)
    broker.replaying = bool(broker.replay_log)
