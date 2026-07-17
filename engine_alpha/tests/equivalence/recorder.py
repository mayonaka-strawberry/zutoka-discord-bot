"""New-engine side of the cross-engine equivalence harness.

Plays a fixed-deck game with a seeded uniform-random policy, recording every
decision as (purpose, kind, payload) into per-player queues, and canonical
snapshots at every turn boundary. The old-engine driver replays the queues
and must produce identical snapshots.
"""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.actions import SELECT_CARD, SELECT_IDENTITY
from engine_alpha.battle import total_power
from engine_alpha.game import Game


def snapshot_new(game: Game) -> tuple:
    """Canonical turn-boundary snapshot (fields shared with the old engine)."""
    state = game.state
    players = []
    for player in state.players:
        players.append((
            player.hp,
            tuple(state.inst_def[i] for i in player.deck),
            tuple(sorted(state.inst_def[i] for i in player.hand)),
            tuple(state.inst_def[i] for i in player.charger),
            tuple(state.inst_def[i] for i in player.abyss),
            state.inst_def[player.battle] if player.battle != -1 else -1,
            state.inst_def[player.set_c] if player.set_c != -1 else -1,
            total_power(state, player),
            player.area_blocked,
            player.hand_bonus,
        ))
    return (state.turn, state.chronos, state.last_battle_winner, tuple(players))


def record_game(seed: int, decks: tuple[list[int], list[int]], max_turns: int = 50):
    """Returns (records, snapshots, night_player, winner).

    records: per-player list of (purpose, kind, payload) where payload is
      - SELECT_CARD: ('pass',) or ('idx', index_into_candidates)
      - SELECT_IDENTITY: ('key', 'XX-YYY')
      - SELECT_NUMBER / BINARY: ('num', value)
    snapshots: list of snapshot_new() taken after every completed turn and
      at game end.
    """
    game = Game(seed=seed, mode="fixed_decks", decks=decks, max_turns=max_turns)
    policy_rng = random.Random(seed ^ 0x5EED)
    records: tuple[list, list] = ([], [])
    snapshots: list[tuple] = []
    night_player = 0 if game.state.players[0].side_is_night else 1

    last_turn = game.state.turn
    while not game.is_terminal():
        request = game.decision_context()
        action = policy_rng.choice(game.legal_actions())
        if request.kind == SELECT_CARD:
            payload = ("pass",) if request.is_pass(action) else ("idx", action)
        elif request.kind == SELECT_IDENTITY:
            payload = ("key", cards.CARD_DB[action].key)
        else:
            payload = ("num", action)
        records[game.state.acting].append((request.purpose, request.kind, payload))
        game.apply(action)
        if game.state.turn != last_turn and not game.is_terminal():
            snapshots.append(snapshot_new(game))
            last_turn = game.state.turn

    snapshots.append(snapshot_new(game))
    return records, snapshots, night_player, game.state.winner


def random_full_pool_deck(rng: random.Random) -> list[int]:
    """A legal 20-card deck sampled from the full 422-card pool."""
    distinct = rng.sample(range(cards.NUM_CARDS), 10)
    deck = []
    for def_index in distinct:
        deck.extend((def_index, def_index))
    return deck
