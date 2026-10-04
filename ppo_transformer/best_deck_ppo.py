"""
Deck strength round robin under the deployed PPO checkpoint.

Every training-pool deck plays every other, with the same checkpoint on both seats,
so only the deck varies. The strongest decks are written as {'guid', 'cards'} entries
(nothing in the bot reads this file); the full standings are printed.

Argmax play can loop within a turn, where the engine's max_turns cap never fires, so
a turn past TURN_DECISION_LIMIT decisions plays seeded random moves until it ends.
Parallel by process (the engine is GIL-bound); throughput is flat past 8 workers.

Usage:
    python -m ppo_transformer.best_deck_ppo
    python -m ppo_transformer.best_deck_ppo --workers 8 --max-decks 20 --games-per-pair 9
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from engine_alpha.game import Game
from model_common.deck_pool import (
    PPO_DECK_POOL_PATH,
    card_references,
    deck_guid,
    describe_deck_pool,
    load_deck_pool_file,
)
from ppo_transformer.inference import PpoAgent, find_checkpoint

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = REPOSITORY_ROOT / 'data' / 'best_decks_ppo.json'
PROGRESS_INTERVAL_PAIRS = 100

# Decisions in one turn before it counts as a loop (a whole game averages about 29).
TURN_DECISION_LIMIT = 300

# Backstop if sampling fails to break a loop. Never reached so far; hitting it stops the run.
DECISION_HARD_LIMIT = 200_000

# Set by _initialize_worker in each process (or the driver, with one worker). Module
# level because Windows spawns workers, so nothing else reaches the child.
_WORKER_POOL: list[list[int]] = []
_WORKER_AGENT: PpoAgent | None = None
_WORKER_GAMES_PER_PAIR = 0
_WORKER_SEED_BASE = 0
_WORKER_PAIRS: list[tuple[int, int]] = []


def _initialize_worker(pool: list[list[int]], pairs: list[tuple[int, int]],
                       games_per_pair: int, seed_base: int) -> None:
    """Per-process setup: load the checkpoint once, and hold torch to one thread so
    workers do not fight over cores."""
    global _WORKER_POOL, _WORKER_AGENT, _WORKER_GAMES_PER_PAIR
    global _WORKER_SEED_BASE, _WORKER_PAIRS

    import torch

    torch.set_num_threads(1)
    _WORKER_POOL = pool
    _WORKER_PAIRS = pairs
    _WORKER_GAMES_PER_PAIR = games_per_pair
    _WORKER_SEED_BASE = seed_base
    _WORKER_AGENT = PpoAgent()
    _WORKER_AGENT._ensure_loaded()


def _play_resolved(decks: tuple[list[int], list[int]], seed: int) -> tuple[int, int]:
    """One game to a real result: (winner, perturbed decision count). Past
    TURN_DECISION_LIMIT decisions in a turn, legal actions are sampled (seeded from the
    game seed) until the turn advances; healthy games never get there."""
    game = Game(seed=seed, mode='fixed_decks', decks=decks)
    sampler = random.Random(seed)
    current_turn = -1
    decisions_this_turn = 0
    perturbed = 0

    for _ in range(DECISION_HARD_LIMIT):
        if game.is_terminal():
            return game.state.winner, perturbed
        if game.state.turn != current_turn:
            current_turn = game.state.turn
            decisions_this_turn = 0
        decisions_this_turn += 1
        if decisions_this_turn > TURN_DECISION_LIMIT:
            action = sampler.choice(game.legal_actions())
            perturbed += 1
        else:
            action = _WORKER_AGENT.act(game)
        game.apply(action)

    raise RuntimeError(
        f'game did not resolve within {DECISION_HARD_LIMIT} decisions '
        f'(seed {seed}, decks {decks[0]} vs {decks[1]})')


def _play_pair(pair_index: int) -> dict:
    """Every game of one deck pair. Seeds derive from `pair_index` alone, so results do
    not depend on worker order. Seats alternate on (game_index + pair_index), evening
    out an odd games_per_pair across opponents; day/night is the engine's coin flip."""
    deck_i, deck_j = _WORKER_PAIRS[pair_index]
    cards_i = _WORKER_POOL[deck_i]
    cards_j = _WORKER_POOL[deck_j]
    wins_i = 0
    wins_j = 0
    draws = 0
    perturbed_games = 0

    for game_index in range(_WORKER_GAMES_PER_PAIR):
        seed = _WORKER_SEED_BASE + pair_index * _WORKER_GAMES_PER_PAIR + game_index
        deck_i_first = (game_index + pair_index) % 2 == 0
        decks = (cards_i, cards_j) if deck_i_first else (cards_j, cards_i)
        winner, perturbed = _play_resolved(decks, seed)
        if perturbed:
            perturbed_games += 1
        if winner == 2:
            draws += 1
        elif (winner == 0) == deck_i_first:
            wins_i += 1
        else:
            wins_j += 1

    return {'pair_index': pair_index, 'deck_i': deck_i, 'deck_j': deck_j,
            'wins_i': wins_i, 'wins_j': wins_j, 'draws': draws,
            'perturbed_games': perturbed_games}


def _format_duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}'


def _accumulate(records: list[dict], result: dict) -> None:
    deck_i = records[result['deck_i']]
    deck_j = records[result['deck_j']]
    deck_i['wins'] += result['wins_i']
    deck_i['losses'] += result['wins_j']
    deck_i['draws'] += result['draws']
    deck_j['wins'] += result['wins_j']
    deck_j['losses'] += result['wins_i']
    deck_j['draws'] += result['draws']


def run_round_robin(pool: list[list[int]], games_per_pair: int, seed_base: int,
                    workers: int, executor_kind: str) -> list[dict]:
    """Play every pair; return a tally record per deck (independent of finish order)."""
    pairs = list(itertools.combinations(range(len(pool)), 2))
    records = [{'index': index, 'wins': 0, 'losses': 0, 'draws': 0}
               for index in range(len(pool))]
    total_games = len(pairs) * games_per_pair
    print(f'{len(pool)} decks, {len(pairs)} pairs, {games_per_pair} games per pair, '
          f'{total_games} games total')

    started = time.perf_counter()
    completed = 0
    perturbed_games = 0

    def report() -> None:
        elapsed = time.perf_counter() - started
        games_done = completed * games_per_pair
        rate = games_done / elapsed if elapsed > 0 else 0.0
        remaining = (total_games - games_done) / rate if rate > 0 else 0.0
        print(f'  {completed}/{len(pairs)} pairs  {games_done}/{total_games} games  '
              f'{rate:.1f} games/s  elapsed {_format_duration(elapsed)}  '
              f'eta {_format_duration(remaining)}')

    if workers <= 1:
        _initialize_worker(pool, pairs, games_per_pair, seed_base)
        for pair_index in range(len(pairs)):
            result = _play_pair(pair_index)
            _accumulate(records, result)
            perturbed_games += result['perturbed_games']
            completed += 1
            if completed % PROGRESS_INTERVAL_PAIRS == 0:
                report()
    else:
        executor_class = (ProcessPoolExecutor if executor_kind == 'process'
                          else ThreadPoolExecutor)
        with executor_class(max_workers=workers, initializer=_initialize_worker,
                            initargs=(pool, pairs, games_per_pair, seed_base)) as executor:
            futures = [executor.submit(_play_pair, pair_index)
                       for pair_index in range(len(pairs))]
            for future in as_completed(futures):
                result = future.result()
                _accumulate(records, result)
                perturbed_games += result['perturbed_games']
                completed += 1
                if completed % PROGRESS_INTERVAL_PAIRS == 0:
                    report()

    report()
    print(f'finished {total_games} games in '
          f'{_format_duration(time.perf_counter() - started)}')
    if perturbed_games:
        print(f'{perturbed_games} game(s) needed sampling to break a livelock '
              f'(argmax cycled within a turn); all resolved to a real winner')
    return records


def rank_decks(pool: list[list[int]], records: list[dict]) -> list[dict]:
    """Ranked entries, strongest first: win rate, then wins, then guid (stable). Only
    `guid` and `cards` reach the output file."""
    entries = []
    for record in records:
        definitions = pool[record['index']]
        games = record['wins'] + record['losses'] + record['draws']
        entries.append({
            'guid': deck_guid(definitions),
            'cards': card_references(definitions),
            'wins': record['wins'],
            'losses': record['losses'],
            'draws': record['draws'],
            'net_wins': record['wins'] - record['losses'],
            'games': games,
            'win_rate': round(record['wins'] / games, 4) if games else 0.0,
        })
    entries.sort(key=lambda entry: (-entry['win_rate'], -entry['wins'], entry['guid']))
    return entries


def write_output(path: Path, payload: dict) -> None:
    """Write via a temporary file so an interrupted run leaves the old one intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Rank decks by round robin under the deployed PPO checkpoint.')
    parser.add_argument('--deck-pool', default=str(PPO_DECK_POOL_PATH),
                        help='Deck pool export to rank (default: data/training_decks_ppo.json)')
    parser.add_argument('--games-per-pair', type=int, default=3,
                        help='Games between each pair of decks (default: 3)')
    parser.add_argument('--top', type=int, default=20,
                        help='How many decks to write out (default: 20)')
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT_PATH),
                        help='Where to write the ranked decks (default: data/best_decks_ppo.json)')
    parser.add_argument('--seed-base', type=int, default=0,
                        help='Base game seed; the run reproduces from it (default: 0)')
    parser.add_argument('--max-decks', type=int, default=0,
                        help='Use only the first N decks of the pool, for a quick run (default: all)')
    parser.add_argument('--workers', type=int, default=8,
                        help='Worker processes; 1 runs in this process (default: 8)')
    parser.add_argument('--executor', choices=('process', 'thread'), default='process',
                        help='Parallelism kind; threads are GIL-bound here (default: process)')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    arguments = parse_arguments(argv)

    checkpoint = find_checkpoint()
    if checkpoint is None:
        print('No PPO checkpoint is deployed. Drop one at model/ppo_transformer '
              'or ppo_transformer/deploy/model.pt and run again.', file=sys.stderr)
        return 1
    print(f'checkpoint: {checkpoint}')

    pool_file = load_deck_pool_file(arguments.deck_pool)
    pool = pool_file.decks
    print(describe_deck_pool(pool, arguments.deck_pool,
                             pool_file.excluded_attribute_names))
    if not pool:
        print(f'No decks to rank. Generate the pool with '
              f'scripts/export_training_decks.py, which writes '
              f'data/training_decks_ppo.json, or point --deck-pool at an '
              f'existing export.', file=sys.stderr)
        return 1
    if arguments.max_decks > 0:
        pool = pool[:arguments.max_decks]
        print(f'limited to the first {len(pool)} deck(s) by --max-decks')
    if len(pool) < 2:
        print('Ranking needs at least two decks.', file=sys.stderr)
        return 1
    if len(pool) < arguments.top:
        print(f'warning: only {len(pool)} deck(s) available, fewer than the '
              f'{arguments.top} requested')

    records = run_round_robin(
        pool, arguments.games_per_pair, arguments.seed_base,
        arguments.workers, arguments.executor)
    entries = rank_decks(pool, records)
    best = entries[:arguments.top]

    # A draw comes only from a mutual deck-out or the engine's turn cap, so report it.
    total_draws = sum(entry['draws'] for entry in entries) // 2
    if total_draws:
        print(f'\nwarning: {total_draws} drawn game(s) - expected none; '
              f'these hit the engine turn cap of 200')

    # Only the decks go in the file; the standings are printed below.
    output_path = Path(arguments.output)
    write_output(output_path, {
        'decks': [{'guid': entry['guid'], 'cards': entry['cards']} for entry in best],
    })

    # Print every deck: pool problems show at the bottom of the table.
    print(f'\nall {len(entries)} decks by win rate:')
    print(f'{"rank":>4}  {"win%":>6}  {"W":>5}  {"L":>5}  {"D":>5}  {"net":>5}  guid')
    for rank, entry in enumerate(entries, start=1):
        marker = '*' if rank <= len(best) else ' '
        print(f'{rank:>4}{marker} {entry["win_rate"] * 100:>5.1f}%  '
              f'{entry["wins"]:>5}  {entry["losses"]:>5}  {entry["draws"]:>5}  '
              f'{entry["net_wins"]:>5}  {entry["guid"]}')
    print(f'\n* = written to {output_path} ({len(best)} deck(s))')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
