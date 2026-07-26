# ppo_transformer

PPO training for the Zutomayo card game: vectorized on-policy rollouts over the
deterministic `engine_alpha` rules engine, a transformer policy/value network
with pointer/identity/number action heads, clipped policy and value losses, an
annealed entropy bonus, and a snapshot opponent pool with arena-gated promotion.

Unlike `alpha_zero`, this stack plays fixed decks only — every game draws both
decks from the training deck pool. Deck building is not part of what it learns.

**`train/` is not in git.** It is gitignored on purpose: the repository carries
only what the Discord bot needs to *play* from a checkpoint (`config.py`,
`net/model.py`, `inference.py`). A fresh clone can run the bot but not train,
and the commands below will be missing.

## Configuration

Defaults live in [config.py](config.py) as a tree of dataclasses — that file is
the tracked, reproducible baseline. `ppo_transformer/.env` layers per-machine
overrides on top of it and is gitignored.

```
PPO_<SECTION>_<FIELD>=value   # e.g. PPO_TRAIN_MINIBATCH_SIZE=2048
PPO_<NAME>=value              # run-level, e.g. PPO_ITERATIONS=2000
```

Precedence, highest first: **CLI flag → process environment → `.env` → dataclass
default.** So a one-off experiment needs no file edit:

```powershell
$env:PPO_TRAIN_GAE_LAMBDA='1.0'; python -m ppo_transformer.train.run_train
```

To list every key with its current value:

```powershell
python -m ppo_transformer.config                          # print the full surface
python -m ppo_transformer.config > ppo_transformer\.env   # regenerate .env
```

Every line in that output is commented, so redirecting it into `.env` documents
the whole surface without changing any behaviour. Generating it from the
dataclasses is deliberate — a hand-maintained template drifts, and this one
cannot.

Bad values fail at startup rather than mid-run: `load_config()` validates that
the opponent mix sums to 1, that `embed_dim` divides by `num_heads`, that the
identity/effect capacities cover the card catalog, that the minibatch fits
inside the rollout, and that warmup fits inside the decay horizon.

## Running

Smoke run first — two tiny iterations, confirms the loop is wired:

```powershell
python -m ppo_transformer.train.run_train --smoke
```

Then the real run, which takes its settings from `.env`:

```powershell
python -m ppo_transformer.train.run_train
python -m ppo_transformer.train.run_train --resume    # continue an existing run
```

Each iteration prints one line:

```
iteration 42: games=13820 samples=65536 loss=1.4402 lr=2.6e-04 (68s)
```

`games` is cumulative; `samples` is the decisions collected this iteration
(`PPO_TRAIN_ROLLOUT_DECISIONS`); `lr` follows the warmup-then-cosine schedule.
Every `PPO_TRAIN_CHECKPOINT_INTERVAL_ITERATIONS` (default 5) the trainer also
runs a promotion gate against the newest snapshot and saves.

## Stopping safely

A run is expected to be interrupted. All three channels do the same thing: set a
flag, let the current iteration finish, then save a checkpoint, publish weights,
and print the resume command.

| Channel | Use when |
|---|---|
| **Ctrl+C** | the run is attached to your terminal |
| **`New-Item ppo_transformer\runs\STOP`** | the run is detached — another terminal, over SSH, under `nohup`. This is the only safe option there. |
| **SIGTERM** (`Stop-Process`) | a process manager is shutting the machine down |

Pressing **Ctrl+C a second time** restores Python's default handler and aborts
immediately — for a run that is genuinely wedged.

The stop is checked at the iteration boundary, because that is the only point
where all state is consistent: rollout buffers are transient and the optimizer
runs to completion inside an iteration. So expect to wait out the current
iteration (a minute or two at default settings). A checkpoint is also written if
the run crashes, so an unexpected error no longer costs an iteration of rollout.

Checkpoints are written to a temp name and renamed, so an interrupt cannot leave
a truncated file where `--resume` would find it.

## Resuming

```powershell
python -m ppo_transformer.train.run_train --resume
```

Picks the newest checkpoint under `runs/checkpoints/` and restores the network,
optimizer, LR scheduler, iteration counter, games played, the snapshot pool, and
the RNG stream. Restoring RNG state matters: without it a resumed run would
replay the same deck draws and opponent choices it would have produced from
scratch.

**If you changed the config since the checkpoint:** any `PPO_NET_*` difference
is refused with a message naming the fields, rather than failing later inside
`load_state_dict` with a tensor-shape error. Other settings are legitimate
mid-run changes and only print a note — though be aware that changing
`PPO_TRAIN_ENTROPY_ANNEAL_ITERATIONS` or the LR horizon retroactively rescales
those schedules, since both are driven by the restored iteration number.

## Runs directory

```
runs/
  checkpoints/iteration_XXXXX.pt   full training state
  snapshots/iteration_XXXXX.pt     promoted opponents (never pruned)
  latest_weights.pt                bare state dict for deployment
  STOP                             create this to stop the run
```

Only the newest `PPO_TRAIN_CHECKPOINT_RETENTION` (default 5) checkpoints are
kept; promoted snapshots are never pruned, since they are reloaded as opponents.
Set the value to `0` to keep everything.

## Deploying a checkpoint

`find_checkpoint()` looks for `ppo_transformer/deploy/model.pt` first, then
`runs/latest_weights.pt`, then the newest `runs/checkpoints/iteration_*.pt`.
Promote a checkpoint by copying it:

```powershell
New-Item -ItemType Directory -Force ppo_transformer\deploy
Copy-Item ppo_transformer\runs\checkpoints\iteration_00420.pt ppo_transformer\deploy\model.pt
```

`PpoAgent` plays argmax over the masked legal actions — a single forward pass
per decision, with no search to configure.

## Troubleshooting

**`cannot resume: the network architecture changed`.** Restore the listed
`PPO_NET_*` values, or start a fresh runs directory.

**`probabilities must sum to 1.0`.** `PPO_TRAIN_P_LATEST_VS_LATEST`,
`P_VS_SNAPSHOT` and `P_VS_RANDOM` are one distribution; `p_vs_random` is the
implicit remainder, so changing one means changing another.

**Entropy collapsing to near zero early.** Raise
`PPO_TRAIN_ENTROPY_BONUS_INITIAL` or lengthen
`PPO_TRAIN_ENTROPY_ANNEAL_ITERATIONS`; a policy that goes deterministic before
the value head is any good will stop improving.

**Out of memory during rollout.** Lower `PPO_TRAIN_VECTORIZED_GAMES` (512
concurrent games by default) before touching the net size. This bites much
later than it used to: the update peaks at ~9 GB under bf16 where fp32 needed
~15.7 GB of a 24 GB card.

## Tuning notes

Applied defaults worth knowing about, and knobs worth trying:

- **Mixed precision (not configurable).** The PPO update and the rollout's
  batched learner forward both run under bf16 autocast on CUDA; master weights
  and the AdamW state stay fp32, and every head output is cast back before the
  categorical, so log-probs, the ratio and both losses are computed in full
  precision. Worth 502→289 ms per minibatch and 1193→1486 rollout decisions/s.
  The cost is ratio noise: the fp32 epoch-1 ratio is essentially exact
  (max `|r-1|` = 8e-6), while bf16 carries mean `|r-1|` = 0.0064, max 0.040 —
  roughly 0.6% multiplicative noise on the policy-gradient term, negligible
  beside the advantage estimator's own variance, with 0% of ratios landing
  outside the 0.2 clip band. Note the rollout autocast is justified by its own
  1.25x, *not* by making the two paths agree: matching precision cuts the ratio
  noise only 1.17x, since the two batch different slot counts and token widths
  and bf16 reduction order is shape-dependent. Gating and snapshot argmax stay
  fp32 — bf16 flips the argmax on ~1% of decisions (near-ties).
- **Snapshot opponents are batched.** Slots waiting on the same snapshot are
  grouped and stepped with one forward per group per round rather than one
  forward per decision — at 512 concurrent games that is a mean batch of 81, so
  ~187 forwards where there were ~15,000. Behaviour is unchanged: batch-1 and
  batched argmax agree on 512/512 real positions, and a full rollout produces
  byte-identical samples either way. `RandomOpponent` still acts inline, since
  it has no forward to batch.
- **`PPO_TRAIN_GAE_LAMBDA` (0.98).** The reward is terminal-only, so lambda
  controls how much of the actual game outcome reaches early decisions: at 0.95
  a decision 50 steps from the end sees it at weight 0.08, at 0.98 it sees 0.36.
  Try `1.0` (pure Monte Carlo, unbiased, higher variance) if early-game play
  looks weak.
- **Learning-rate schedule (`PPO_TRAIN_WARMUP_ITERATIONS`,
  `LEARNING_RATE_FINAL`, `LEARNING_RATE_DECAY_ITERATIONS`).** Warmup then cosine
  decay, stepped once per iteration. Set the decay horizon to the iteration
  count you actually plan to run, or the LR will not finish decaying.
- **`PPO_TRAIN_NORMALIZE_ADVANTAGE_PER_BATCH` (true).** Normalizes advantages
  once over the whole rollout instead of per minibatch — a 1024-sample minibatch
  out of 65536 is a noisy estimate of the mean and std. Set false for the old
  per-minibatch behaviour.
- **`PPO_TRAIN_P_VS_RANDOM` (0.15).** Games against a uniform-random opponent
  are cheap early signal and mostly wasted compute later; consider decaying it
  toward 0.05 once snapshots exist.
- **Snapshot sampling (`PPO_TRAIN_SNAPSHOT_HARDNESS_BIAS`, 0).** `0` weights
  snapshots by `p(1-p)`, favouring evenly matched opponents; `1` shifts weight
  toward snapshots the learner still loses to.
- **`PPO_TRAIN_VALUE_CLIP_RANGE` (0.2).** Value clipping is of debated benefit
  in PPO; worth an ablation.
- `PPO_TRAIN_PPO_EPOCHS`, `CLIP_RANGE` and `MINIBATCH_SIZE` are the remaining
  standard PPO knobs. There is currently no KL early-stop.
