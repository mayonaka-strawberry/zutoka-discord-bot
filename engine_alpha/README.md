# engine_alpha

An AlphaZero-style bot for ZUTOMAYO CARD (uniguri) that learns **how to play
and how to build decks simultaneously**, built on a from-scratch,
fully-resumable game engine. Completely self-contained: nothing here imports
from the `zutomayo` package (only `zutomayo/data/cards.json` is read as data),
and nothing in the Discord bot or `/playuniguri` is touched. The entire
directory is gitignored.

## Why a new engine

The old engine implements card effects as async coroutines that prompt for
choices mid-execution. A suspended coroutine frame cannot be cloned, so tree
search could only ever branch on "which cards to set" — never on effect
targets, numbers, resolution order, or mulligans (the v3 pilot's documented
blocker). engine_alpha replaces coroutines with an **explicit state machine**:
`Game.legal_actions() / apply(action) / clone()` works at *every* decision
point, so MCTS can branch anywhere. Mid-effect state lives in explicit
continuation frames (program counter + registers), cloned in ~2 µs.

## Key design points

- **Effect IR**: all 250 card effects are written in a small declarative
  language (`effects/catalog_data.py`) interpreted by the engine
  (`effects/interpreter.py`). The same IR is auto-featurized into 128-d
  vectors for the network (`effects/features.py`) — the representation can
  never drift from the behavior. 8 gnarly effects (CHAOS bombs, nested
  effect borrowing, deck-peek areas) use custom step-machine handlers.
- **Deck building as game moves**: every game starts with a 40-ply
  alternating draft from the full 422-card pool, searched by the same MCTS
  and learned by the same network as in-game moves. A hall-of-fame league
  (past promoted checkpoints + their best decks) keeps the meta diverse.
- **Deterministic chance**: shuffles are counter-based functions of the
  state's RNG (key, counter), so clones agree on all futures — no chance
  nodes needed in the tree.
- **Verified against the old engine**: a cross-engine equivalence harness
  replays identical decision scripts through both engines and compares
  state snapshots every turn (plus prompt-sequence identity). ~7,000 games
  verified so far; it caught exactly one real porting bug (02-015), which is
  fixed.

## Status

| Milestone | Built | Gate |
|---|---|---|
| M0 scaffolding (cards DB, RNG, config) | yes | PASSED (card DB tests) |
| M1 core engine, vanilla rules | yes | PASSED (13 property tests, 1M-step fuzz clean, 3,730 games/s, 2.3 µs clone, hand-verified transcript) |
| M2 effect IR, all 250 effects, equivalence | yes | ~7,000 games equivalent; **full 10k gate: run it yourself (below)** |
| M3 encoding + net (14.7M params) + MCTS | yes | smoked; **baseline gate: run it yourself** |
| M4 training pipeline (workers, GPU evaluator, buffer, league, gating, resume) | yes | smoked (inline train, resume, gating, 2-worker pool); **12h run: yours** |
| M5 scale training | n/a | **yours** (command below) |
| M6 cross-engine vs v2 | yes | smoked (2 clean games vs the trained v2 checkpoint) |

## Layout

```
config.py              hyperparameter dataclass tree + .env loading (load_config)
.env                   ALL training parameters (see naming convention below)
cards.py               card DB: 422 CardDefs, dense vocabularies, flat lookup arrays
rng.py                 counter-based deterministic RNG (splitmix64 Fisher-Yates)
state.py               GameState/PlayerState/Frame: __slots__, ints, fast_clone
actions.py             the 4 DecisionRequest kinds + purpose tags
zones.py               placement triggers (agent-based vs location-based semantics)
battle.py              attack precedence chain, battle resolution, win checks
game.py                the resumable phase driver (Game facade)
draft.py               draft legality
effects/               IR schema, interpreter, conditions, selectors, catalog
                       (250 entries), custom handlers, removal, turn-end, featurizer
encoding/observation.py  state -> 172-token observation
net/model.py           UniguriNet: transformer + pointer/identity/number + value heads
net/wrapper.py         cross-process GPU inference service (multi-model, hot reload)
mcts/mcts.py           PUCT, player-0-frame values, batched leaves + virtual loss
selfplay/              game records, replay buffer (npz shards), league, workers
train/                 losses + Trainer (checkpoint/resume, gating, TensorBoard)
eval/                  arena, baselines, gating, cross_engine (vs v2)
tests/                 unit/property/ruling tests + tests/equivalence/ (the harness)
scripts/               all entry points (below)
runs/                  training artifacts (checkpoints, buffer, league, logs)
```

## Configuration: `engine_alpha/.env`

Every training parameter lives in [.env](.env) with its default documented.
Naming: `ALPHA_<SECTION>_<FIELD>` maps onto the `Config` dataclass tree
(e.g. `ALPHA_MCTS_SIMULATIONS_IN_GAME=256`, `ALPHA_TRAIN_BATCH_SIZE=1024`,
`ALPHA_LEAGUE_GATING_WIN_RATE=0.55`); run-level settings are plain
`ALPHA_<NAME>` (`ALPHA_WORKERS`, `ALPHA_ITERATIONS`, `ALPHA_DEVICE`, ...).
Process environment variables override the file; CLI flags override both.
This file is engine_alpha's own — it is unrelated to the repo-root `.env`
(Discord bot token).

## Commands

All commands run from the repo root (`d:\GitHub\zutoka-discord-bot`).

### Tests and engine verification (fast)

```bash
# Full test suite: card DB, property/invariant, rulings, equivalence smoke (~5 s)
python -m pytest engine_alpha/tests/ -q

# Performance gate: games/s and clone latency (~30 s)
python -m engine_alpha.scripts.bench_engine

# Invariant fuzzer: random games checking zone conservation etc. (~30 s per 1M steps)
python -m engine_alpha.scripts.fuzz --steps 1000000

# Human-readable transcript of one game (for rule verification)
python -m engine_alpha.scripts.transcript --seed 11
python -m engine_alpha.scripts.transcript --seed 11 --draft
```

### Milestone gates (long-running — run in order)

```bash
# M2 gate (~30-45 min): 10k cross-engine games, zero divergences,
# every effect resolved >= 50 times (phase 2 auto-targets rare effects)
python -m engine_alpha.tests.equivalence.run_equivalence --games 10000

# M3 gate: MCTS vs Random (>=95%) and GreedyHeuristic (>=60%) over 200 games.
# NOTE: with an UNTRAINED net this may fall short (the value head is noise);
# that is not a defect — re-run with --checkpoint after training.
python -m engine_alpha.scripts.run_gate_m3 --games 200 --sims 256
python -m engine_alpha.scripts.run_gate_m3 --games 200 --sims 256 --checkpoint engine_alpha/runs/checkpoints/step_XXXXXXXX.pt
```

### Self-play and training

```bash
# Self-play smoke: a few games, sample counts, games/s (~10 s)
python -m engine_alpha.scripts.run_selfplay --games 4 --sims 16

# Training pipeline smoke: 2 tiny iterations end to end (~1 min)
python -m engine_alpha.scripts.run_train --smoke

# Full training (M5). All parameters from engine_alpha/.env; --resume
# restores net/optimizer/scheduler/buffer/league after any interruption.
python -m engine_alpha.scripts.run_train --resume

# ... or override run settings on the command line:
python -m engine_alpha.scripts.run_train --workers 12 --iterations 1000 --games-per-iter 64 --train-steps 400 --resume
```

### Monitoring and results

```bash
# Learned deck meta: top decks per hall-of-fame snapshot + most-used cards
python -m engine_alpha.scripts.inspect_decks

# Loss curves, gating scores, buffer size, Elo
tensorboard --logdir engine_alpha/runs/logs
```

### Cross-engine evaluation vs the old v2 bot (M6, optional)

```bash
# Plays inside the OLD engine: v2 natively, the new bot via a lockstep mirror.
# Requires zutomayo/models_trained_v2/checkpoint_*.pt.
python -m engine_alpha.eval.cross_engine --games 20 --sims 128 --checkpoint engine_alpha/runs/checkpoints/step_XXXXXXXX.pt
```

## Recommended sequence

1. `pytest` + `bench_engine` + `fuzz` (sanity, minutes)
2. `run_equivalence --games 10000` (the M2 hard gate — nothing downstream
   matters if the rules are wrong)
3. `run_train --smoke`, then the full `run_train --resume`
4. Periodically: `inspect_decks`, TensorBoard, and `run_gate_m3 --checkpoint ...`
5. When training looks strong: `cross_engine` to measure against the v2 bot
