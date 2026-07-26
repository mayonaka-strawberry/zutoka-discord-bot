"""Termination-reason helpers shared by the training stacks.

The engine records only `state.winner` for the outcome, plus one
reason-like pair of fields for the CHAOS bank-or-lose cards:
`self_defeat_player` / `self_defeat_turn`, set by `apply_self_defeat()` in
engine_alpha/effects/interpreter.py. That function is reached only from the
insufficient-Abyss branch of the five CHAOS effects (the `lose_game` opcode
used by 04-027 / 04-028 / 04-105, and the custom handlers for 04-006 /
04-088), so it never fires for a battle-HP loss, a deck-out, area removal or
the turn cap.

Both trainers shape the terminal signal for that one case; the predicate
lives here so the two stacks agree on what counts.
"""

from __future__ import annotations


def chaos_self_defeat_loser(state) -> int:
    """Seat that LOST the game to its own CHAOS self-defeat, else -1.

    Guards two cases where `self_defeat_player` alone would mislead:
    `check_win` awards the win on higher HP when both players are at or below
    zero, so the self-defeating player can still win; and a game can end in a
    draw (winner 2) after a self-defeat. Only a game the self-defeater
    actually lost gets shaped.
    """
    self_defeat_player = getattr(state, "self_defeat_player", -1)
    if self_defeat_player == -1:
        return -1
    if state.winner != 1 - self_defeat_player:
        return -1
    return self_defeat_player
