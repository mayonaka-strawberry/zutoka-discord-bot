"""
Deck strength round robin under the deployed PPO checkpoint.

Every deck in the training pool plays every other deck, with the same checkpoint
on both seats, so the only variable in a game is the deck. Decks are ranked by
win rate and the strongest are written to a JSON file as `{'guid', 'cards'}`
entries, `cards` being the shape decks take everywhere else in the repository.
Nothing of the run itself is written: the file is a deck source, and the full
standings are printed instead.

Games are played to a real winner. Argmax play can livelock - a deterministic
policy answers a repeatable position the same way forever - and the engine's
max_turns cannot catch it, because that cap is only tested when a turn advances.
A turn that issues too many decisions is broken out of by sampling; see
`_play_resolved`.

Parallelism is by process, not by thread: most of a game is the engine's step
loop, which is pure Python and so GIL-bound, and a thread pool measured a 1.4x
ceiling. Throughput is flat in the worker count past a handful - 10.3, 10.1 and
9.7 games/s at 8, 16 and 24 workers - so the default is 8, which was fastest and
is also cheapest in memory. Each worker loads the checkpoint once and runs torch
single-threaded, without which the processes only contend.

Usage:
    python -m ppo_transformer.best_deck_ppo
    python -m ppo_transformer.best_deck_ppo --workers 8
    python -m ppo_transformer.best_deck_ppo --max-decks 8 --games-per-pair 2
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
    DEFAULT_DECK_POOL_PATH,
    card_references,
    deck_guid,
    describe_deck_pool,
    load_deck_pool,
)
from ppo_transformer.inference import PpoAgent, find_checkpoint

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = REPOSITORY_ROOT / 'data' / 'best_decks_ppo.json'
PROGRESS_INTERVAL_PAIRS = 100

# Decisions inside a single turn before the game is treated as livelocked.
# A whole game averages about 29 decisions, so 300 in one turn is not a long
# turn, it is a cycle. The engine's own max_turns cannot catch this: the cap is
# only tested when a turn advances (engine_alpha/game.py), and a game stuck
# within a turn never gets there.
TURN_DECISION_LIMIT = 300

# Backstop if sampling somehow fails to break a cycle. Never reached in
# practice - one random action was enough for every livelock observed - so
# hitting it means something is wrong enough that a recorded result would be
# untrustworthy, and the run stops instead.
DECISION_HARD_LIMIT = 200_000

# Set by _initialize_worker in each pool process, and by the driver itself when
# running with a single worker. Module level because Windows spawns workers
# rather than forking them, so nothing else survives into the child.
_WORKER_POOL: list[list[int]] = []
_WORKER_AGENT: PpoAgent | None = None
_WORKER_GAMES_PER_PAIR = 0
_WORKER_SEED_BASE = 0
_WORKER_PAIRS: list[tuple[int, int]] = []


def _initialize_worker(pool: list[list[int]], pairs: list[tuple[int, int]],
                       games_per_pair: int, seed_base: int) -> None:
    """Per-process setup: one checkpoint load, torch held to one thread.

    Loading here rather than per task matters - the checkpoint takes ~2.6s to
    read against roughly a second of work per pair. Pinning torch to a single
    intra-op thread matters more: without it every worker defaults to one thread
    per core and the processes spend their time fighting each other for cores.
    """
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
    """One game to a real result. Returns (winner, perturbed decision count).

    Argmax play can livelock. The policy is deterministic, so a position that
    offers a repeatable option is answered the same way every time and the game
    cycles forever - one matchup in the first full run span 20,000 decisions
    inside a single turn without advancing it. A human or a sampling agent
    escapes by eventually choosing differently.

    So when a turn has issued `TURN_DECISION_LIMIT` decisions, this samples
    uniformly from the legal actions until the turn moves on, then hands control
    back to the policy. That is the smallest intervention that breaks a cycle:
    the livelock observed needed exactly one sampled action, and healthy games
    never reach the limit, so their play is bit-for-bit what argmax alone would
    produce. The rng is seeded from the game seed, so a perturbed game still
    replays identically.
    """
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
    """Every game between one pair of decks, as seen from the pair's two decks.

    The unit of work is a pair rather than a game so that the cost of handing
    work to a process is amortised over several seconds of play, and so that all
    of the seeding and seat logic stays on one side of the process boundary.

    Seeds derive from `pair_index` alone, so a pair plays the same games no
    matter which worker picks it up or in what order, and the whole tournament
    reproduces from `--seed-base`. The policy is a plain argmax, so the seed is
    the only thing that separates one game in a pair from the next.

    Seats alternate on `(game_index + pair_index)`. An odd `games_per_pair`
    hands one deck an extra game in seat 0; keying the parity on the pair index
    as well as the game index alternates which deck that is from one opponent to
    the next, so over the 110 opponents a deck faces it comes out even. Which
    seat is NIGHT is left to the engine's own seed-driven coin flip, as it is
    for a real single match.
    """
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
    """Play every pair; return a tally record per deck.

    Results are accumulated as they complete. Order does not matter: each result
    names the two decks it belongs to and its seeds came from its pair index, so
    the totals are the same whatever sequence the workers finish in.
    """
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
    """Tally records as ranked output entries, strongest first.

    Ranked on win rate, so a deck is not rewarded for drawing rather than
    losing. Every deck plays the same number of games, so this only separates
    decks that wins-minus-losses would tie, and only when their draw counts
    differ. Wins and then guid break the remaining ties, keeping the order total
    and stable between runs that score the same.

    The tallies ride along on each entry for the sort and for the standings
    table `main` prints; only `guid` and `cards` are written to the output file.
    """
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
    parser.add_argument('--deck-pool', default=str(DEFAULT_DECK_POOL_PATH),
                        help='Deck pool export to rank (default: data/training_decks.json)')
    parser.add_argument('--games-per-pair', type=int, default=3,
                        help='Games between each pair of decks (default: 3)')
    parser.add_argument('--top', type=int, default=15,
                        help='How many decks to write out (default: 15)')
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

    pool = load_deck_pool(arguments.deck_pool)
    print(describe_deck_pool(pool, arguments.deck_pool))
    if not pool:
        print(f'No decks to rank. Generate the pool with '
              f'scripts/export_training_decks.py, or point --deck-pool at an '
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

    # Every game should resolve to a winner. A draw here can only come from the
    # engine's own turn cap, which a 20-turn game never approaches, so it is
    # worth saying out loud rather than letting it sit in a column.
    total_draws = sum(entry['draws'] for entry in entries) // 2
    if total_draws:
        print(f'\nwarning: {total_draws} drawn game(s) - expected none; '
              f'these hit the engine turn cap of 200')

    # The output is a deck source and nothing else: whatever reads it resolves
    # `cards` and ignores the rest, so the tallies and the run's own bookkeeping
    # stay out of the file. The standings live in the table printed below.
    output_path = Path(arguments.output)
    write_output(output_path, {
        'decks': [{'guid': entry['guid'], 'cards': entry['cards']} for entry in best],
    })

    # Every deck, not just the ones written out: the losing end of the table is
    # where a pool problem shows up, and it is gone once the process exits.
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
