"""Equivalence comparison: record on the new engine, replay on the old,
diff the snapshot streams."""

from __future__ import annotations

import random

from .recorder import record_game, random_full_pool_deck
from .driver import replay_old_game


class EquivalenceError(AssertionError):
    pass


_FIELDS = ("hp", "deck", "hand(sorted)", "charger", "abyss", "battle", "set_c",
           "total_power", "area_blocked", "hand_bonus")


def _diff(new_snapshot, old_snapshot, *, ignore_turn: bool) -> list[str]:
    problems = []
    if not ignore_turn and new_snapshot[0] != old_snapshot[0]:
        problems.append(f"turn: new={new_snapshot[0]} old={old_snapshot[0]}")
    if new_snapshot[1] != old_snapshot[1]:
        problems.append(f"chronos: new={new_snapshot[1]} old={old_snapshot[1]}")
    if new_snapshot[2] != old_snapshot[2]:
        problems.append(f"last_battle_winner: new={new_snapshot[2]} old={old_snapshot[2]}")
    for player_index in (0, 1):
        for field_index, field_name in enumerate(_FIELDS):
            new_value = new_snapshot[3][player_index][field_index]
            old_value = old_snapshot[3][player_index][field_index]
            if new_value != old_value:
                problems.append(
                    f"P{player_index} {field_name}: new={new_value} old={old_value}")
    return problems


def check_game(seed: int, decks, max_turns: int = 50) -> tuple[int, int]:
    """Runs one seeded game through both engines. Returns
    (turns_compared, winner). Raises EquivalenceError on divergence."""
    records, new_snapshots, night_player, new_winner = record_game(seed, decks, max_turns)
    old_snapshots, old_winner = replay_old_game(seed, decks, records, night_player, max_turns)

    if len(new_snapshots) != len(old_snapshots):
        raise EquivalenceError(
            f"seed {seed}: snapshot count mismatch new={len(new_snapshots)} "
            f"old={len(old_snapshots)} (winners new={new_winner} old={old_winner})")

    for index, (new_snapshot, old_snapshot) in enumerate(zip(new_snapshots, old_snapshots)):
        is_final = index == len(new_snapshots) - 1
        both_draw = is_final and new_winner == 2 and old_winner == 2
        problems = _diff(new_snapshot, old_snapshot, ignore_turn=both_draw)
        if problems:
            raise EquivalenceError(
                f"seed {seed}: divergence at snapshot {index} "
                f"(turn {new_snapshot[0]}):\n  " + "\n  ".join(problems))

    if new_winner != old_winner:
        raise EquivalenceError(
            f"seed {seed}: winner mismatch new={new_winner} old={old_winner}")
    return len(new_snapshots), new_winner


def run_bulk(n_games: int, seed_base: int = 0, verbose: bool = True):
    """Bulk equivalence run over random full-pool decks. Returns effect
    coverage counter (effect id -> times resolved in the new engine)."""
    from engine_alpha.effects import interpreter
    from engine_alpha.cards import EFFECT_IDS

    coverage: dict[str, int] = {}
    original_start = interpreter.start_effect

    def counting_start(state, owner, iid, effect_index):
        coverage[EFFECT_IDS[effect_index]] = coverage.get(EFFECT_IDS[effect_index], 0) + 1
        original_start(state, owner, iid, effect_index)

    interpreter.start_effect = counting_start
    try:
        for game_number in range(n_games):
            seed = seed_base + game_number
            deck_rng = random.Random(seed ^ 0xDECC5)
            decks = (random_full_pool_deck(deck_rng), random_full_pool_deck(deck_rng))
            check_game(seed, decks)
            if verbose and (game_number + 1) % 200 == 0:
                print(f"  {game_number + 1}/{n_games} games equivalent; "
                      f"{len(coverage)}/247 effects covered")
    finally:
        interpreter.start_effect = original_start
    return coverage
