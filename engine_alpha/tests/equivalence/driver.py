"""Old-engine side of the cross-engine equivalence harness.

Replays a recorded decision script into the REAL old engine
(zutomayo.engine.uniguri_env_v2.HeadlessGameEnvV2) with:
- the coin flip patched to the recorded side assignment,
- random.shuffle patched to reproduce the new engine's counter-based
  permutations (same rng key, same event order),
- a ScriptedAgent answering every choose_* callback from the per-player
  queues, asserting purpose/kind agreement (prompt-sequence identity).

Snapshots are taken at every turn boundary in the same canonical form as
recorder.snapshot_new and compared by the caller.

Must run with the repo root on sys.path (the zutomayo package imports
top-level constants).
"""

from __future__ import annotations

import asyncio
import random as _random

from engine_alpha import cards as new_cards
from engine_alpha.actions import (
    P_MULLIGAN, P_INITIAL_CARD, P_SET_SLOT_A, P_SET_SLOT_B, P_EFFECT_ORDER,
    P_EFFECT_TARGET, P_EFFECT_NUMBER, P_NAME_GUESS, P_SKIP_SWAP, P_CHRONOS_VALUE,
    PURPOSE_NAMES,
)
from engine_alpha.rng import shuffled as alpha_shuffled

_NUMBER_PURPOSES = (P_EFFECT_NUMBER, P_SKIP_SWAP, P_CHRONOS_VALUE)
_COST_REDUCING_IDS = ("02-006", "04-065")


class PromptSequenceError(AssertionError):
    pass


class ScriptedAgent:
    """Answers the old engine's agent callbacks from a recorded queue."""

    def __init__(self, player_index: int, queue: list) -> None:
        self.player_index = player_index
        self.queue = list(queue)
        self.position = 0

    def _pop(self, expected_purposes: tuple[int, ...]):
        if self.position >= len(self.queue):
            raise PromptSequenceError(
                f"P{self.player_index}: old engine asked for "
                f"{[PURPOSE_NAMES[p] for p in expected_purposes]} but the script is exhausted")
        purpose, kind, payload = self.queue[self.position]
        if purpose not in expected_purposes:
            raise PromptSequenceError(
                f"P{self.player_index} at #{self.position}: old engine asked for "
                f"{[PURPOSE_NAMES[p] for p in expected_purposes]} but script has "
                f"{PURPOSE_NAMES[purpose]}")
        self.position += 1
        return purpose, payload

    def _peek_purpose(self):
        if self.position >= len(self.queue):
            return None
        return self.queue[self.position][0]

    # --- BotAgent interface -------------------------------------------------

    def choose_redraw(self, hand):
        marked = []
        candidates = list(hand)
        while True:
            _, payload = self._pop((P_MULLIGAN,))
            if payload[0] == "pass":
                return marked
            chosen = candidates[payload[1]]
            marked.append(chosen)
            candidates.remove(chosen)

    def choose_initial_battle_card(self, hand):
        _, payload = self._pop((P_INITIAL_CARD,))
        return hand[payload[1]]

    def choose_cards_to_set(self, hand, max_cards):
        _, payload = self._pop((P_SET_SLOT_A,))
        if payload[0] == "pass":
            return []
        first = hand[payload[1]]
        if self._peek_purpose() == P_SET_SLOT_B:
            _, payload_b = self._pop((P_SET_SLOT_B,))
            if payload_b[0] != "pass":
                remaining = [c for c in hand if c is not first]
                return [first, remaining[payload_b[1]]]
        return [first]

    def choose_effect_order(self, eligible):
        # The new engine enforces forced-first cost reducers and records
        # sequential picks over the remaining pool; rebuild that exact order.
        forced = [ci for ci in eligible if ci.card.effect in _COST_REDUCING_IDS]
        selectable = [ci for ci in eligible if ci.card.effect not in _COST_REDUCING_IDS]
        ordered = list(forced)
        while len(selectable) > 1:
            _, payload = self._pop((P_EFFECT_ORDER,))
            chosen = selectable[payload[1]]
            ordered.append(chosen)
            selectable.remove(chosen)
        ordered.extend(selectable)
        return ordered

    def choose_effect_card(self, cards_list):
        _, payload = self._pop((P_EFFECT_TARGET,))
        return cards_list[payload[1]]

    def choose_effect_number(self, min_value, max_value):
        _, payload = self._pop(_NUMBER_PURPOSES)
        value = payload[1]
        if not min_value <= value <= max_value:
            raise PromptSequenceError(
                f"P{self.player_index}: number {value} outside old engine's "
                f"range [{min_value}, {max_value}]")
        return value

    def choose_effect_text(self):
        _, payload = self._pop((P_NAME_GUESS,))
        return payload[1]


class _ShuffleReplayer:
    """random.shuffle replacement reproducing the new engine's permutations.

    The new engine consumes rng counter 0 for the coin flip and one counter
    per subsequent shuffle event, in a fixed order. The old engine performs
    the same shuffle events in the same order, so applying the permutation
    generated from (key, running_counter) reproduces them exactly.
    """

    def __init__(self, rng_key: int) -> None:
        self.rng_key = rng_key
        self.counter = 1  # 0 was the coin flip

    def __call__(self, items) -> None:
        permutation = alpha_shuffled(list(range(len(items))), self.rng_key, self.counter)
        self.counter += 1
        items[:] = [items[i] for i in permutation]


def snapshot_old(game_state) -> tuple:
    def def_index(card_instance):
        return new_cards.CARD_INDEX[(card_instance.card.pack, card_instance.card.id)]

    players = []
    for player in game_state.players:
        players.append((
            player.hp,
            tuple(def_index(c) for c in player.deck),
            tuple(sorted(def_index(c) for c in player.hand)),
            tuple(def_index(c) for c in player.power_charger),
            tuple(def_index(c) for c in player.abyss),
            def_index(player.battle_zone) if player.battle_zone is not None else -1,
            def_index(player.set_zone_c) if player.set_zone_c is not None else -1,
            player.total_power,
            player.area_enchant_blocked,
            player.hand_size_bonus,
        ))
    if game_state.last_battle_winner is None:
        last_winner = -1
    else:
        last_winner = 0 if game_state.last_battle_winner == "agent_0" else 1
    return (game_state.turn, game_state.chronos, last_winner, tuple(players))


def replay_old_game(seed: int, decks: tuple[list[int], list[int]], records,
                    night_player: int, max_turns: int = 50):
    """Replays the script into the old engine. Returns (snapshots, winner).
    Winner: 0/1 player index, 2 for in-progress at the turn cap (draw)."""
    from zutomayo.engine import game_controller
    from zutomayo.engine.uniguri_env_v2 import HeadlessGameEnvV2
    from zutomayo.enums.phase import Phase
    from zutomayo.enums.result import Result
    from zutomayo.data.card_loader import load_cards

    old_cards = load_cards()
    by_key = {(c.pack, c.id): c for c in old_cards}
    deck_cards_0 = [by_key[(new_cards.CARD_DB[d].pack, new_cards.CARD_DB[d].number)] for d in decks[0]]
    deck_cards_1 = [by_key[(new_cards.CARD_DB[d].pack, new_cards.CARD_DB[d].number)] for d in decks[1]]

    agent_0 = ScriptedAgent(0, records[0])
    agent_1 = ScriptedAgent(1, records[1])
    env = HeadlessGameEnvV2(
        agent_0=agent_0, agent_1=agent_1, intermediate_reward_scale=0.0,
        deck_cards_for_player_0=deck_cards_0, deck_cards_for_player_1=deck_cards_1,
    )

    replayer = _ShuffleReplayer(seed)
    original_randint = game_controller.randint
    original_shuffle = _random.shuffle
    # Old GameController: coin_flip == 0 -> player 0 sits NIGHT-side.
    game_controller.randint = lambda a, b: 0 if night_player == 0 else 1
    _random.shuffle = replayer
    try:
        return asyncio.run(_play(env, max_turns))
    finally:
        game_controller.randint = original_randint
        _random.shuffle = original_shuffle


async def _play(env, max_turns: int):
    """Replicates HeadlessGameEnvV2.play_full_game phase-for-phase, taking a
    snapshot after every completed turn."""
    from zutomayo.enums.phase import Phase
    from zutomayo.enums.result import Result

    env.reset()
    game_state = env.game_state
    turn_manager = env.turn_manager
    snapshots = []

    # Turn 1 (no SET_CARDS / swaps / TURN_END_EFFECTS)
    game_state.turn = 1
    game_state.chronos_at_turn_start = game_state.chronos
    game_state.current_phase = Phase.ADVANCE_CHRONOS
    for player in game_state.players:
        turn_manager.advance_chronos(player)
    game_state.current_phase = Phase.PROCESS_EFFECTS
    priority = game_state.priority_player
    await env.effect_engine.process_effects(game_state, priority)
    turn_manager.check_win_condition()
    if game_state.result == Result.IN_PROGRESS:
        await env.effect_engine.process_effects(game_state, 1 - priority)
        turn_manager.check_win_condition()
    if game_state.result == Result.IN_PROGRESS:
        game_state.current_phase = Phase.BATTLE
        turn_manager.resolve_battle()
        turn_manager.check_win_condition()
    if game_state.result == Result.IN_PROGRESS:
        game_state.current_phase = Phase.END_TURN
        for player in game_state.players:
            turn_manager.end_turn(player)
        for player in game_state.players:
            player.hand_size_bonus += player.pending_hand_size_bonus
            player.pending_hand_size_bonus = 0
        env.effect_engine.check_area_enchant_removal(game_state, turn_manager, end_of_turn=True)
        if game_state.result == Result.IN_PROGRESS:
            env.effect_engine.save_battle_characters(game_state)
            turn_manager.reset_turn_flags()

    while game_state.result == Result.IN_PROGRESS and game_state.turn < max_turns:
        game_state.turn += 1
        snapshots.append(snapshot_old(game_state))
        game_state.chronos_at_turn_start = game_state.chronos
        await env._do_headless_turn()

    snapshots.append(snapshot_old(game_state))
    if game_state.result == Result.PLAYER_1_WIN:
        winner = 0
    elif game_state.result == Result.PLAYER_2_WIN:
        winner = 1
    else:
        winner = 2
    return snapshots, winner
