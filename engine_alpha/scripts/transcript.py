"""Print a human-readable transcript of one random game (M1 hand-verification).

Every decision, phase transition, chronos movement, battle outcome and draw
is printed so a full game can be checked line-by-line against the printed
rules (start guide + rule guide).

Usage: python -m engine_alpha.scripts.transcript [--seed N] [--draft]
"""

from __future__ import annotations

import argparse
import random
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from engine_alpha import cards
from engine_alpha.actions import PURPOSE_NAMES
from engine_alpha.battle import get_effective_attack, total_power
from engine_alpha.game import Game
from engine_alpha.state import PHASE_NAMES
from engine_alpha.tests.conftest import random_vanilla_deck


def card_name(state, instance_id: int) -> str:
    d = cards.CARD_DB[state.inst_def[instance_id]]
    return f"{d.key} {d.name} (clk{d.clock} N{d.attack_night}/D{d.attack_day} cost{d.power_cost} stp{d.send_to_power})"


def describe_state(game: Game) -> str:
    state = game.state
    lines = [f"  turn={state.turn} phase={PHASE_NAMES[state.phase]} chronos={state.chronos} "
             f"({'NIGHT' if state.is_night else 'DAY'}) last_battle_winner={state.last_battle_winner}"]
    for player in state.players:
        battle = card_name(state, player.battle) if player.battle != -1 else "-"
        lines.append(
            f"  P{player.index} [{'NIGHT' if player.side_is_night else 'DAY'}-side] hp={player.hp} "
            f"power={total_power(state, player)} atk={get_effective_attack(state, player)} "
            f"deck={len(player.deck)} hand={len(player.hand)} charger={len(player.charger)} "
            f"abyss={len(player.abyss)} battle={battle}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    if args.draft:
        game = Game(seed=args.seed, mode="draft")
    else:
        game = Game(seed=args.seed, mode="fixed_decks",
                    decks=(random_vanilla_deck(rng), random_vanilla_deck(rng)))

    step = 0
    last_turn_phase = (-1, -1)
    while not game.is_terminal():
        state = game.state
        if (state.turn, state.phase) != last_turn_phase:
            print(describe_state(game))
            last_turn_phase = (state.turn, state.phase)
        request = game.decision_context()
        action = rng.choice(game.legal_actions())
        detail = ""
        if request.kind == 0:  # SELECT_CARD
            if request.is_pass(action):
                detail = "PASS"
            else:
                detail = card_name(state, request.candidates[action])
        elif request.kind == 1:  # SELECT_IDENTITY
            d = cards.CARD_DB[action]
            detail = f"{d.key} {d.name}"
        else:
            detail = str(action)
        print(f"#{step:<4} P{state.acting} {PURPOSE_NAMES[request.purpose]:<14} -> {detail}")
        game.apply(action)
        step += 1

    state = game.state
    print(describe_state(game))
    print(f"\nGAME OVER: winner={state.winner} after turn {state.turn} "
          f"HP {state.players[0].hp}/{state.players[1].hp}")


if __name__ == "__main__":
    main()
