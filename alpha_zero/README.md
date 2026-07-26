# alpha_zero

AlphaZero-style training for the Zutomayo card game: PUCT search over the
deterministic `engine_alpha` rules engine, a transformer policy/value network,
and a hall-of-fame league with promotion gating. Deck building is part of the
same loop rather than a separate model — `Game(mode="draft")` makes the first 40
plies an alternating 20-pick draft searched by the same network, so the value
head credits deck choices with the same signal it credits plays.

**Most of this package is not in git.** `train/`, `selfplay/`, `eval/`,
`scripts/` and `net/wrapper.py` are gitignored on purpose: the repository
carries only what the Discord bot needs to *play* from a checkpoint
(`config.py`, `net/model.py`, `mcts/`, `inference.py`). A fresh clone can run
the bot but not train, and the commands below will be missing.

## Configuration

Defaults live in [config.py](config.py) as a tree of dataclasses — that file is
the tracked, reproducible baseline. `alpha_zero/.env` layers per-machine
overrides on top of it and is gitignored.

```
ALPHA_<SECTION>_<FIELD>=value   # e.g. ALPHA_MCTS_SIMULATIONS_IN_GAME=256
ALPHA_<NAME>=value              # run-level, e.g. ALPHA_WORKERS=24
```

Precedence, highest first: **CLI flag → process environment → `.env` → dataclass
default.** So a one-off experiment needs no file edit:

```powershell
$env:ALPHA_MCTS_SIMULATIONS_IN_GAME='512'; python -m alpha_zero.scripts.run_train
```

To list every key with its current value:

```powershell
python -m alpha_zero.config              # print the full surface
python -m alpha_zero.config > alpha_zero\.env    # regenerate .env from scratch
```

Every line in that output is commented, so redirecting it into `.env` documents
the whole surface without changing any behaviour. Generating it from the
dataclasses is deliberate — a hand-maintained template drifts, and this one
cannot.

Bad values fail at startup rather than thousands of steps in: `load_config()`
validates that opponent-sampling probabilities sum to 1, that `embed_dim`
divides by `num_heads`, that the identity/effect capacities cover the card
catalog, and that warmup fits inside the decay horizon.

`ALPHA_ENGINE_*` keys are an exception worth knowing: they are recorded in
checkpoints for provenance, but the engine uses inline constants, so setting
them changes nothing.

## Running

Smoke run first — two tiny iterations, a couple of minutes, confirms the whole
loop is wired:

```powershell
python -m alpha_zero.scripts.run_train --smoke
```

Then the real run, which takes its settings from `.env`:

```powershell
python -m alpha_zero.scripts.run_train
python -m alpha_zero.scripts.run_train --resume    # continue an existing run
```

Each iteration prints one line:

```
iter 12: games=1560 buffer=98432 step=2840 steps=180/400 loss=2.9114 lr=2.9e-04 seat0=0.51 draw=0.02 snap_wr=0.58 len=63 (selfplay 41s, train 12s)
```

| Field | Meaning |
|---|---|
| `games` / `buffer` | cumulative self-play games; replay-buffer samples |
| `step` | optimizer steps taken so far |
| `steps=180/400` | steps actually taken vs requested — see troubleshooting |
| `seat0` | seat-0 win rate in symmetric games; drifting far from 0.50 means a seat bias |
| `draw` | draw rate |
| `snap_wr` | learner win rate against frozen league snapshots |
| `len` | samples per game |

Other entry points: `run_selfplay.py` (self-play only), `run_gate_m3.py`
(measure against the Random and GreedyHeuristic baselines),
`export_best_decks.py` (publish the league's best decks to
`zutomayo/bot_decks.json`), `inspect_decks.py` (deck-meta report).

## Stopping safely

A run is expected to be interrupted. All three channels do the same thing: set a
flag, let the current game and optimizer step finish, then save a checkpoint,
publish weights, shut the worker pool down in order, and print the resume
command. Nothing is lost.

| Channel | Use when |
|---|---|
| **Ctrl+C** | the run is attached to your terminal |
| **`New-Item alpha_zero\runs\STOP`** | the run is detached — another terminal, over SSH, under `nohup`. This is the only safe option there. |
| **SIGTERM** (`Stop-Process`) | a process manager is shutting the machine down |

Pressing **Ctrl+C a second time** restores Python's default handler and aborts
immediately — for a run that is genuinely wedged. That path can lose the current
iteration, so prefer waiting for the clean stop.

With workers running, expect the wind-down to take a few seconds: the trainer
abandons any queued matchups, workers finish the game in hand, and the pool
joins with a timeout. The `STOP` file is deleted once acted on, so the next run
does not immediately stop again.

Stopping mid-iteration is safe. Samples already ingested are in the buffer, the
league is saved after every game, and checkpoints are written to a temp name and
renamed, so an interrupt cannot leave a truncated file where `--resume` would
find it.

## Resuming

```powershell
python -m alpha_zero.scripts.run_train --resume
```

Picks the newest checkpoint under `runs/checkpoints/` and restores the network,
EMA shadow weights, optimizer, LR scheduler, step counter, sample counters,
games played, best checkpoint, and both RNG streams. The replay buffer and
league registry are read from disk independently, so they carry over too.
Restoring RNG state matters: without it a resumed run would replay the same deck
draws and matchups it would have produced from scratch.

**If you changed the config since the checkpoint:** any `ALPHA_NET_*` difference
is refused with a message naming the fields, rather than failing later inside
`load_state_dict` with a tensor-shape error that says nothing about which
variable caused it. Other sections (learning rate, opponent mix, gating) are
legitimate mid-run changes and only print a note.

## Runs directory

```
runs/
  checkpoints/step_XXXXXXXX.pt   full training state
  buffer/                        replay shards + index.json
  league/league.json             snapshot registry, Elo, deck memory
  logs/                          TensorBoard scalars (pip install tensorboard)
  latest_weights.pt              EMA weights the worker pool serves
  weights_version.txt
  STOP                           create this to stop the run
```

Checkpoints are large, so only the newest `ALPHA_TRAIN_CHECKPOINT_RETENTION`
(default 5) are kept. The best checkpoint and every league snapshot are never
pruned — they are reloaded as opponents during self-play. Set the value to `0`
to keep everything.

One cosmetic edge case: an interrupt between writing a buffer shard and updating
its index can orphan a shard file. The index filters missing files and ignores
unindexed ones, so this leaks a little disk but never corrupts the buffer.

## Deploying a checkpoint

`find_checkpoint()` looks for `alpha_zero/deploy/model.pt` first, then the newest
`runs/checkpoints/step_*.pt`. Promote a checkpoint by copying it:

```powershell
New-Item -ItemType Directory -Force alpha_zero\deploy
Copy-Item alpha_zero\runs\checkpoints\step_00120000.pt alpha_zero\deploy\model.pt
```

The live agent defaults to **search** mode (`ALPHA_LIVE_SIMULATIONS`, default
64) rather than a single policy forward. Training and gating both select for
strength *with* search, so policy-only play gives up most of what the checkpoint
was chosen for. Set `ALPHA_LIVE_MODE=policy` if latency ever matters more.

The cost is bounded: `ModelDecisionAdapter` runs `act` through
`asyncio.to_thread` behind a 45-second watchdog and falls back to a legal action
on any failure, so search never blocks the Discord event loop or hangs a game.
Subtree reuse across decisions means the tree is not rebuilt every turn.

## Troubleshooting

**`steps=0/400` or far below the requested count.** Not a bug. The sample-reuse
throttle (`ALPHA_TRAIN_MAX_SAMPLE_REUSE`, default 8) caps optimizer steps at
`samples_generated x reuse / batch_size`, so the trainer waits on self-play
rather than overfitting the buffer. If it happens every iteration, self-play is
your bottleneck: raise `ALPHA_WORKERS` or `ALPHA_GAMES_PER_ITER`, or lower
`ALPHA_MCTS_SIMULATIONS_IN_GAME`. Raising `ALPHA_TRAIN_STEPS` will do nothing.

**Set the LR horizon from measured throughput.** `ALPHA_TRAIN_LEARNING_RATE_DECAY_STEPS`
is a cosine horizon and should match the optimizer steps the run will actually
reach — which the throttle above puts well below `iterations x train_steps`.
Read the `steps=` field for ~20 iterations, multiply, and set it. Guessing high
means the LR never decays.

**`cannot resume: the network architecture changed`.** Restore the listed
`ALPHA_NET_*` values, or start a fresh runs directory.

**`probabilities must sum to 1.0`.** The four `ALPHA_LEAGUE_P_*` values are an
opponent-sampling distribution; `p_pool_decks` is the implicit remainder, so
changing one means changing another.

**Orphaned worker processes.** Should not happen — workers ignore SIGINT and are
shut down by the parent. If a hard kill leaves some behind:
`Get-Process python | Stop-Process -Force`.

## Tuning notes

Applied defaults worth knowing about, and knobs worth trying:

- **Net size (`ALPHA_NET_*`, currently 8 layers x 384).** Self-play is the
  binding constraint at 256 simulations per decision, so the net is sized for
  cheap forwards (~14M parameters) rather than capacity. More games at a smaller
  net generally beats fewer games at a larger one under a fixed wall clock.
  `8 x 512` (~25M) is the next step up if games/hour turns out comfortable.
  Changing this invalidates existing checkpoints, so decide early.
- **`ALPHA_MCTS_TEMPERATURE_MOVES` (30).** Counts in-game *decisions*, not
  turns; micro-decision chains mean one turn is several decisions. Too low and
  self-play goes deterministic right after the draft, starving the buffer of
  opening diversity.
- **`ALPHA_LEAGUE_GATING_SIMULATIONS` (128).** The budget both sides get during
  promotion gating. Setting it to `ALPHA_LIVE_SIMULATIONS` makes promotion
  measure deployed strength directly.
- **Playout cap (`ALPHA_MCTS_PLAYOUT_CAP_FRACTION`, 0.25).** A quarter of games
  run entirely at a reduced budget, and those positions are still recorded as
  policy targets. KataGo's original scheme excludes reduced-search positions
  from the policy target and keeps them value-only; until that is implemented,
  lowering the fraction is the cheap mitigation.
- **`ALPHA_TRAIN_VALUE_LOSS_WEIGHT` (1.0).** Weight on the win/draw/loss value
  cross-entropy relative to the policy loss; 0.5-1.5 is the usual range.
- **`ALPHA_TRAIN_GRADIENT_CHECKPOINTING` (true).** Trades ~30% training speed
  for memory. Now that the net is smaller, try turning it off.
- **`ALPHA_MCTS_USE_GUMBEL_ROOT` (false).** Breaks near-ties among root visit
  counts by prior-plus-Gumbel score instead of index order. Worth trying at
  small simulation budgets.
- `ALPHA_MCTS_C_PUCT_INIT`, `ALPHA_MCTS_DIRICHLET_EPSILON`,
  `ALPHA_TRAIN_MAX_SAMPLE_REUSE` and `ALPHA_TRAIN_EMA_DECAY` are the remaining
  standard AlphaZero knobs.
