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

### The horizon block

Six settings are one unit and live together at the top of `.env` rather than in
their own sections, because a partial edit fails quietly:

- `PPO_ITERATIONS`
- `PPO_TRAIN_LEARNING_RATE_DECAY_ITERATIONS` — **must equal** `PPO_ITERATIONS`,
  or the cosine never reaches `LEARNING_RATE_FINAL`
- `PPO_TRAIN_ENTROPY_ANNEAL_ITERATIONS`
- `PPO_TRAIN_CHECKPOINT_INTERVAL_ITERATIONS`
- `PPO_TRAIN_GATING_WIN_RATE` — a fixed threshold gets *less* selective as the
  gate count grows, since each gate is an independent chance to promote on noise
- `PPO_TRAIN_SNAPSHOT_CAPACITY` — the pool evicts FIFO, so capacity is really a
  window over the most recent promotions, and how much of a run that window
  spans depends entirely on the horizon

A key in this block must not also be set live further down the file. `.env` is
read by python-dotenv when it is importable, and for a key repeated inside one
file dotenv keeps the **last** occurrence — so the block's value would be
silently overridden by the section below. This is what the `-> Horizon block`
pointer comments in the sections are protecting.

`.env` ships a short pilot block active and a long-run block commented out;
exactly one should be uncommented. The anneal fraction is deliberately *not*
constant between them — exploration need is roughly absolute rather than
proportional, so a short run wants a larger fraction (60%) than a long one
(40%), or it spends most of its life at the entropy floor with a value head that
has barely trained.

**Switching horizons requires a fresh runs directory.** `--resume` restores the
iteration counter, so pointing a long-run block at a finished short run replays
both schedules from that iteration and jumps the learning rate back up ~10x.

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

Each iteration prints three lines, plus a fourth on gating iterations:

```
iteration 42: games=13820 samples=65536 total_loss=0.2109
  learning_rate=2.6e-04 epochs=2/3 elapsed=119s
  policy_loss=-0.0041 value_loss=0.4361 entropy=1.823
  clip_fraction=0.112 approximate_kl_divergence=0.0071 explained_variance=+0.564
  gate 0.583 vs best -> promoted (pool_size: 9, gpu_memory 4.1G)
```

`games` is cumulative; `samples` is the decisions collected this iteration
(`PPO_TRAIN_ROLLOUT_DECISIONS`); `learning_rate` follows the warmup-then-cosine
schedule; `epochs` is how many of `PPO_TRAIN_PPO_EPOCHS` actually ran before the
`PPO_TRAIN_TARGET_KL` early stop fired. Every
`PPO_TRAIN_CHECKPOINT_INTERVAL_ITERATIONS` the trainer also runs a promotion
gate and saves. The gate plays the **newest** snapshot in the pool, not the
strongest — the console still labels it `vs best`, which the pool no longer
tracks.

`learning_rate` is the rate that iteration actually trained at. In runs logged
before 2026-07-29 it was read after the scheduler stepped, so those older
`metrics.jsonl` files are one iteration ahead: their iteration 1 reports the
rate iteration 2 would use.

Lines 3 and 4 are the split, averaged over every minibatch actually run:

| Field | What it means |
|---|---|
| `policy_loss` | Clipped surrogate. Hovers near zero by construction — the ratio starts at exactly 1 on fresh data, so this does **not** trend as the policy improves. |
| `value_loss` | Clipped value MSE. Against terminal `+/-1` rewards, a value head predicting 0 scores ~1.0; lower is better but it never reaches 0, since the outcome is genuinely uncertain from an early position. |
| `entropy` | Nats over the legal actions. Falling steadily toward 0 is the failure mode to watch — see Troubleshooting. |
| `clip_fraction` | Fraction of samples the 0.2 clip bit on. Healthy is ~0.05-0.20. Near 0 means updates are too timid for the clip range; above ~0.3 means the policy moves further per iteration than the trust region intends. |
| `approximate_kl_divergence` | Schulman's low-variance KL estimator between the old and new policy. Also the early-stop trigger — see `PPO_TRAIN_TARGET_KL` below. |
| `explained_variance` | Of the value head, `1 - Var(target - value) / Var(target)`, on-policy and pre-update. This is the value-head progress metric: scale-free, comparable across runs, and rising from ~0 toward 1. |

Labels are spelled out to match the keys they come from. Every field, plus the
gate result and VRAM, is also appended to `runs/metrics.jsonl` — one JSON object
per line, readable with `model_common.metrics_log.read_metrics`. The keys there
are namespaced (`policy/clip_fraction`, `value/explained_variance`) and are the
stable record; the console labels are display only.

### The KL early stop

`PPO_TRAIN_TARGET_KL` (default 0.05) caps how far one rollout is allowed to move
the policy. At the end of each epoch, if that epoch's mean
`approximate_kl_divergence` exceeded the target, the remaining epochs are
skipped — they would be training on data the policy has already left behind.
Checked at the epoch boundary rather than mid-epoch, so a short update is never
biased by which samples the shuffle happened to put last.

It is self-regulating: it fires only on iterations that overshoot, and once the
per-step KL falls, all `PPO_TRAIN_PPO_EPOCHS` epochs run again with no config
change. Prefer tuning this over cutting `PPO_TRAIN_PPO_EPOCHS` or the learning
rate, both of which slow every iteration whether or not it needed slowing. Set
it to `0.0` to disable.

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

**What the snapshot pool keeps, and what it loses.** Only the snapshot
identifiers are checkpointed, so membership and order survive — the gate still
compares against the correct newest entry. Two things do not survive. Per-entry
win rates are not saved, so every entry is rebuilt at `0.5`, which is the *peak*
of the sampling weight curve: the pool goes uniform at maximum weight and the
prioritization re-converges over roughly one to two iterations. And a snapshot
whose file has gone missing is skipped **silently**, shrinking the pool without a
message — nothing prunes `runs/snapshots/`, so this needs an external cause, but
a disk cleanup is one.

**If you changed the config since the checkpoint:** any `PPO_NET_*` difference
is refused with a message naming the fields, rather than failing later inside
`load_state_dict` with a tensor-shape error. Other settings are legitimate
mid-run changes and only print a note — though be aware that changing
`PPO_TRAIN_ENTROPY_ANNEAL_ITERATIONS` or the LR horizon retroactively rescales
those schedules, since both are driven by the restored iteration number.
Raising `PPO_TRAIN_SNAPSHOT_CAPACITY` on a resume does not backfill either,
though not because of trimming — with a larger capacity the pool never trims.
The checkpoint records only the identifiers that survived eviction, so the older
snapshots are simply not in the payload to replay, even though their files are
still under `runs/snapshots/`. The extra slots therefore fill only as new
promotions arrive. Lowering capacity applies immediately and keeps the newest N.

## Runs directory

```
runs/
  checkpoints/iteration_XXXXX.pt   full training state
  snapshots/iteration_XXXXX.pt     promoted opponents (never pruned)
  latest_weights.pt                bare state dict for deployment
  metrics.jsonl                    one JSON record per iteration, append-only
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
Copy-Item ppo_transformer\runs\checkpoints\iteration_00320.pt ppo_transformer\deploy\model.pt
```

`PpoAgent` plays argmax over the masked legal actions — a single forward pass
per decision, with no search to configure.

## Troubleshooting

**`cannot resume: the network architecture changed`.** Restore the listed
`PPO_NET_*` values, or start a fresh runs directory.

**`probabilities must sum to 1.0`.** `PPO_TRAIN_P_LATEST_VS_LATEST`,
`P_VS_SNAPSHOT` and `P_VS_RANDOM` are one distribution; `p_vs_random` is the
implicit remainder, so changing one means changing another.

**`total_loss` is not going down.** It is not supposed to, and it is the wrong
number to read. `loss/total` is `policy_loss + value_loss_weight * value_loss -
entropy_bonus * entropy`; the policy term is a surrogate re-zeroed against fresh
on-policy data every rollout and does not trend, and the entropy term is ~0.01
of a ~2-nat quantity. So the printed figure is dominated by `0.5 * value_loss`,
measured against a target that gets *harder* as the opponent pool strengthens.
Expect it to plateau, and to rise after a run of promotions.

Read `explained_variance` and the gate line instead. `explained_variance` rising
toward 1 means the value head is learning; a gate win rate at or above
`PPO_TRAIN_GATING_WIN_RATE` means the policy is beating its own past self, which
is the only direct evidence that training is working. A `total_loss` that drops
unusually low (well under 0.05) is more likely entropy collapse or a value head
fitting noise than a good sign — check `entropy` on the same line.

Give the gate room before judging a run. The first gate is uninformative: with an
empty pool there is no opponent, so the win rate is reported as `1.000` and the
snapshot is promoted unconditionally. After a rejection the pool does not
advance, so the next gate plays the same older snapshot and covers two intervals
of progress rather than one — expect win rates to look higher after a rejection.

Note what the pool is *not*. It evicts FIFO at `PPO_TRAIN_SNAPSHOT_CAPACITY`, so
it is a window over the most recent promotions rather than a full league, and the
gate plays only the newest entry. Both mean a policy can drift around a strategy
loop — beating its recent self, and so passing gates, while losing to its own
policy from far earlier in the run. If strength plateaus while gates keep passing,
suspect that before suspecting the learning rate. The window covers most of a
320-iteration run and only a small fraction of a 4800-iteration one, which is why
capacity sits in the horizon block.

**`clip_fraction` above ~0.3 and `approximate_kl_divergence` above ~0.05,
persistently.** The updates are leaving the trust region: the later epochs are
training on data the policy has already moved away from. Lower
`PPO_TRAIN_TARGET_KL` and watch `epochs` on line 2 start reporting fewer than
`PPO_TRAIN_PPO_EPOCHS`. Do this before reaching for the learning rate — a
promotion cadence that is working does not need a slower optimizer, just a
shorter update. Conversely, `clip_fraction` near 0 with `epochs` always at the
full count means the updates are too timid for the clip range.

**Entropy collapsing to near zero early.** Raise
`PPO_TRAIN_ENTROPY_BONUS_INITIAL` or lengthen
`PPO_TRAIN_ENTROPY_ANNEAL_ITERATIONS`; a policy that goes deterministic before
the value head is any good will stop improving.

**Out of memory during rollout.** Lower `PPO_TRAIN_VECTORIZED_GAMES` (512
concurrent games by default) before touching the net size. This bites much
later than it used to: the update peaks at ~9 GB under bf16 where fp32 needed
~15.7 GB of a 24 GB card.

**Out of memory late in a long run.** Should no longer happen. Snapshot
opponents are cached on the GPU by path, and the cache is pruned to current pool
membership after every promotion, so residency is capped at
`PPO_TRAIN_SNAPSHOT_CAPACITY` networks (~3.9 GB at 30 x 32.3M fp32 params).
Before that pruning existed, every promotion pinned another 129 MB for the rest
of the run, which put a several-thousand-iteration run over a 24 GB card
regardless of batch size. `system/vram_allocated_gb` in `metrics.jsonl` should
plateau across promotions rather than stepping up each gate.

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
- **Iteration cost.** ~119.5 s at the settings above, measured 2026-07-29 over
  iterations 16-31 of a live run (averaged after the first snapshot promotion —
  earlier iterations are cheaper because there is no opponent net to run). So
  320 iterations is ~10.6 h and 4800 is ~6.6 d. Gating iterations cost ~45 s
  extra for the 200-game series.
- **`PPO_TRAIN_GAE_LAMBDA` (0.98).** The reward is terminal-only, so lambda
  controls how much of the actual game outcome reaches early decisions: at 0.95
  a decision 50 steps from the end sees it at weight 0.08, at 0.98 it sees 0.36.
  Try `1.0` (pure Monte Carlo, unbiased, higher variance) if early-game play
  looks weak.
- **CHAOS self-defeat rewards (`PPO_TRAIN_SELF_DEFEAT_LOSS_REWARD` -2.0,
  `PPO_TRAIN_SELF_DEFEAT_WIN_REWARD` 0.25).** The five CHAOS bank-or-lose cards
  end the game immediately when the Abyss minimum is not met. That is a
  self-inflicted blunder rather than a normal loss, so the terminal reward is
  replaced for both seats: the self-defeating player is punished harder than a
  normal loss, and the opponent is credited far less than an earned win so free
  wins are not something the policy learns to play for. Applies only to that
  termination — battle-HP losses, deck-outs and draws keep the engine's `+/-1`,
  as does a CHAOS card whose requirement was met. Gated by
  `model_common.termination.chaos_self_defeat_loser`, which also skips the case
  where the self-defeater still wins on the `check_win` HP tiebreak. Advantage
  normalization is one affine transform over the whole rollout, so the relative
  magnitudes survive into the policy gradient; only the global scale is absorbed.
  The opponent-pool win-rate bookkeeping and gating stay on the true winner.
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
- **`PPO_TRAIN_TARGET_KL` (0.05).** The KL early stop described above. Added
  after the first pilot ran at `clip_fraction` ~0.51 and
  `approximate_kl_divergence` ~0.11 for fifteen straight iterations — the whole
  192-step update ran with nothing watching how far the policy had moved. The
  value is a first cut from that measurement (~0.035-0.04 KL per epoch); worth
  re-tuning once the run has a longer gate history to compare against.
- `PPO_TRAIN_PPO_EPOCHS`, `CLIP_RANGE` and `MINIBATCH_SIZE` are the remaining
  standard PPO knobs.
