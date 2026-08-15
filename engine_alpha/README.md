# engine_alpha

A from-scratch, fully-resumable game engine for ZUTOMAYO CARD (uniguri).
Self-contained: nothing here imports from the `zutomayo` package (only
`zutomayo/data/cards.json` is read as data). The AlphaZero training stack
that was developed alongside this engine lives at the repo root in
`alpha_zero/`; a PPO stack lives in `ppo_transformer/`.

## Why a new engine

The old engine implements card effects as async coroutines that prompt for
choices mid-execution. A suspended coroutine frame cannot be cloned, so tree
search could only ever branch on "which cards to set" — never on effect
targets, numbers, resolution order, or mulligans. engine_alpha replaces
coroutines with an **explicit state machine**:
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
- **Deck building as game moves**: a game can start with a 40-ply
  alternating draft from the full 422-card pool (`mode="draft"`), searched
  and learned exactly like in-game moves, or with fixed decks
  (`mode="fixed_decks"`).
- **Deterministic chance**: shuffles are counter-based functions of the
  state's RNG (key, counter), so clones agree on all futures — no chance
  nodes needed in the tree. Day/night sides are decided by the first draw
  off that stream; `Game(..., night_player=0|1)` overrides the result for
  callers that decide sides themselves (the TCG series, where the previous
  match's loser picks). The draw is consumed either way, so an overridden
  game and a flipped one agree on every shuffle.
- **Engine events**: a `GameState` may carry an `event_sink` list
  (`events.py`); rule functions append observation-only int tuples (draws,
  placements, battle results, phase changes, card reveals, ...) for external
  drivers to narrate. `fast_clone` always detaches the sink, so search clones
  are silent and pay only a `None` check. `EVENT_CARDS_REVEALED` is the one
  variable-arity layout - the `reveal_reg` / `reveal_hand` ops change no zone,
  so this event is the only trace a reveal leaves, and its card tail is empty
  when a player chose to reveal nothing.
- **Verified against the old engine** (historical): during development, a
  cross-engine equivalence harness replayed identical decision scripts
  through both engines and compared state snapshots every turn (plus
  prompt-sequence identity). The final gate run: 500 games, zero
  divergences, 240/247 dispatchable effects exercised (~7,000 further games
  were verified during development; one real porting bug found and fixed,
  02-015). The harness was retired together with the legacy engine and is
  no longer in the tree; ongoing behavior guarantees come from the ruling
  tests, the invariant fuzzer, and the 24-game match regression suite
  (`tests/run_match_regression.py`).
- **Deliberate divergences from the old engine.** Equivalence was the bar
  during the port, not afterwards: where the old behavior contradicts an
  official ruling, the ruling wins. The attack model
  (`battle.get_effective_attack`) folds modifiers in resolution order with a
  per-step clamp instead of summing them, and treats 04-099 as a point-in-time
  set rather than a lock (official Q&A No.40/54/60/68/82). The same change
  retired the old engine's `enemy_atk_eq0_no_override` quirk, so
  04-034/04-039/04-084/04-101 -- four cards with identical text -- finally
  behave identically.

## Rules-compliance audit, 2026-08-13

The engine was audited end to end against the official Q&A (104 entries, the
public Meilisearch index behind <https://zutomayocard.net/qa/>), Ground Rules
ver 1.0.1 (2026-08-08), and the Japanese card text in `cards.json` -- which was
verified identical to the official card database, errata included. All 253
effects were compared against their JP text. Most divergences were in the shared
rule layer, though that sweep was not exhaustive -- the follow-up review below found
two card-level bugs it missed. Fixes, each covered by a test in
`tests/test_rulings.py` that cites its source:

- **Day/night crossings** (`conditions.py` `turn_became`): family D fires when
  the named crossing happened at least once in the turn, read off the per-step
  `GF_DAY_TO_NIGHT` / `GF_NIGHT_TO_DAY` flags. Comparing the turn-start period
  against the current one missed a wrap or a reverted change (Q&A No.17/18).
- **Setting zero cards** (`game.py`): slot A is no longer passable while the
  hand has cards (Ground Rules 5.2.1.5, Q&A No.4). Slot B stays passable.
- **The game ends the instant HP reaches 0** (`battle.record_hp_zero`): the
  winner is fixed at that moment and the effect VM abandons pending frames, so
  a turn-end heal can no longer revive a dead player (Q&A No.41, No.34, No.92;
  Ground Rules 1.2.3/5.4.1). A double knock-out resolves as "first to reach 0
  loses" (user ruling 2026-08-13); the engine never produces an HP draw.
- **Deck shortfall from an effect** (`interpreter` draw/mill ops): a player who
  cannot supply the cards an effect names loses, instead of the count being
  silently clamped (Ground Rules 8.2.1/8.2.2, Q&A No.70).
- **Simultaneous deck-out** (`game._ph_end_turn`): a draw when neither player
  can make the mandatory end-of-turn draw (Ground Rules 5.4.3.1). `_end_turn_for`
  now reports the failure instead of writing a winner per player.
- **Public-zone selections** (`custom.shade_use_two`): 04-002 must pick 1 to 2
  from the power charger, not 0 to 2 (Q&A No.79, Ground Rules 1.3.5.1).
  Hidden-zone picks keep their 0 minimum (Q&A No.90).
- **Mandatory hand reveal** (`catalog_data.py`): 04-032/04-008/04-097 reveal the
  hand whenever their power cost is met; only the attack bonus is conditional
  (Q&A No.89). Previously the reveal was folded into the attribute test and so
  never happened at all.
- **03-058 / 03-085 self-removal** (`removal.py`): their text says すぐに, so the
  30-damage threshold moved out of the turn-end window (Q&A No.16). NOTE: as first
  written this landed the check at a phase boundary *after* that window, which was
  worse than the pre-audit behaviour; see the remediation section below.
- **Turn-end effect ordering** (`game._ph_turn_end_effects`, `turn_end.py`): the
  window re-reads the priority player from the Chronos medal and resolves their
  batch first, and a player holding several turn-end effects orders their own
  via the existing `P_EFFECT_ORDER` prompt (Q&A No.96/102, Ground Rules
  5.2.10.2/10.2.4). It used to run in a fixed `(0, 1)` order.
- **03-064** (`state.ATTACK_MOD_ADD_OWN_HP`): each side adds its remaining HP at
  attack determination, not when the area enchant resolved (Q&A No.33).
- **04-099 and the power gate** (`battle.get_effective_attack`): an unmet power
  cost zeroes the final attack including a 04-099 set, which Q&A No.82 places
  in the same ordered modifier sequence as the bonuses (Ground Rules 2.3.6/7.1.2,
  Q&A No.40/73). This replaces the engine's earlier set-beats-the-gate ruling.

One correction to the above, found by the follow-up review: the 04-099 ruling is
**not** supported by Q&A No.82 (which settles resolution order only) nor by
GR 7.1.2 (which is scoped to attack that was *added*, and 04-099 sets). The
authorities are GR 2.3.6 and 5.1.3.2, with Q&A No.73 as the worked example. GR
1.3.1 (card text outranks the rules) is the counter-argument; it was considered
and rejected.

## Follow-up review and remediation, 2026-08-14

Seven independent agents re-checked the audit against the same sources. The rule
*behaviour* above held up, but the rework around it had introduced defects that a
green suite and a green replay baseline both missed. All are fixed, each with a
regression test citing its source:

- **Area enchants worded 「すぐに」 left play a phase too late.** The audit moved
  03-058/03-085's threshold into `check_area_removal`, whose only call sites ran
  after the turn-end window -- so the heal and the clock advance still fired, worse
  than before the audit. `battle.check_damage_triggered_removal` now runs the moment
  HP changes, scoped via `check_area_removal(damage_only=True)` to just 03-058,
  03-085 and 04-091 so the other seventeen predicates keep their audited timings.
  This also fixes 04-091 per Q&A No.80. (Q&A No.16, GR 8.1.2.)
- **03-027's turn-end damage belonged to the victim.** It is the caster's effect
  (Q&A No.25), so it now resolves in the caster's priority batch and the caster
  orders it. This also removed an unshowable instance from the ordering prompt.
- **The ordering prompt could emit an unmappable instance**, crashing
  `observation.encode` in roughly 2% of games with 03-027 in the pool, and hanging
  the alpha_zero trainer. Items with no representative card are no longer offered
  for ordering, and the answer is resolved by index rather than by instance id.
- **04-002 could not terminate.** Q&A No.79 removed the zero option, and 04-002 is
  itself a SHADE character, so it re-selected itself forever. Candidates now exclude
  any card already resolving further up the chain -- the narrowest rule that
  terminates while leaving Q&A No.83's nesting and No.49 intact.
- **03-097 moved the wrong card**: it put the revealed card on the opponent's
  charger, when Q&A No.45 says the revealed card goes back on top of their deck and
  03-097 itself is what moves.
- **Six reveals never happened.** The name-guess family and 03-097/03-103 read a card
  without emitting `EVENT_CARDS_REVEALED` (GR 10.2.1/10.4.1). Emitted inside the
  handlers, so no IR changed.
- **Discord offered a dead "Set nothing" button** on the single-slot set view, which
  the broker dropped, stalling the prompt for the full timeout and then setting a
  card for the player; three in a row forfeited.
- Smaller: 02-015 dropped its tail when a nested effect ended the game; 01-092 /
  04-089 / 02-015 skipped the deck-shortfall loss (GR 8.2.1); 01-026 manufactured a
  day/night crossing a rewind should not create (Q&A No.17); `PF_ATTACK_BONUS` went
  stale while being a live NN input; 03-058 now heals once per copy (Q&A No.26,
  user-confirmed) rather than once per window.

03-055's third termination condition (Q&A No.28) was investigated and **does not
need a fix**: every area-interfering effect removes 03-055, and each of those paths
already clears the block. `test_qa_28_all_three_03_055_block_terminations_hold`
pins it.

`tests/test_defect_sweep.py` is the gate these defects needed. It forces decks per
defect group rather than sampling randomly (unbiased decks give a given card ~2.4%
exposure, and 150 such games caught none of this), encodes the observation at every
decision, caps frame depth and decision count so a hang fails instead of hanging,
and audits area-enchant end conditions on the turn-end phase boundary. With the
removal hook disabled it fails 65/150; with it enabled, 0/150.

The observation and featurizer layout is unchanged throughout (`FEATURE_DIM` 160,
`EFFECT_FEATURES` (254, 160), `N_PLAYER_FLAGS` 17, `MAX_TOKENS` 172), and the
deployed PPO checkpoint still loads `strict=True`. Note that a strict load is *not*
evidence the catalog is unchanged: `effect_features` is a non-persistent buffer and
is rebuilt from the live catalog, so it never appears in the state dict. Five effect
rows changed value (01-092, 04-008, 04-032, 04-089, 04-097), which is real
off-policy drift on those cards. Both training stacks were smoke-run after the
changes. The 24-game baseline was regenerated: all 24 winners are unchanged, no
decision stream moved, and the only narration change is the new 03-103 reveal line.

## Layout

```
config.py              EngineConfig constants (documentary; engine uses inline values)
cards.py               card DB: 422 CardDefs, dense vocabularies, flat lookup arrays
rng.py                 counter-based deterministic RNG (splitmix64 Fisher-Yates)
state.py               GameState/PlayerState/Frame: __slots__, ints, fast_clone
actions.py             the 4 DecisionRequest kinds + purpose tags
events.py              observation-only engine event constants and tuple layouts
zones.py               placement triggers (agent-based vs location-based semantics)
battle.py              attack modifier fold, battle resolution, win checks
game.py                the resumable phase driver (Game facade)
draft.py               draft legality
baselines.py           RandomAgent / GreedyHeuristicAgent (pure-engine agents)
effects/               IR schema, interpreter, conditions, selectors, catalog
                       (250 entries), custom handlers, removal, turn-end, featurizer
encoding/observation.py  state -> 172-token observation
tests/                 card-DB, invariant, ruling, event, and coverage-playout tests
scripts/               engine verification entry points (below)
```

## Commands

All commands run from the repo root.

```bash
# Full test suite: card DB, property/invariant, rulings, events, coverage playouts (~5 s)
python -m pytest engine_alpha/tests/ -q

# Performance gate: games/s and clone latency (~30 s)
python -m engine_alpha.scripts.bench_engine

# Invariant fuzzer: random games checking zone conservation etc. (~30 s per 1M steps)
python -m engine_alpha.scripts.fuzz --steps 1000000

# Human-readable transcript of one game (for rule verification)
python -m engine_alpha.scripts.transcript --seed 11
python -m engine_alpha.scripts.transcript --seed 11 --draft
```

Training-stack commands (self-play, training, gating, deck inspection,
TensorBoard) live with the stacks: see `alpha_zero/scripts/` (module paths
`alpha_zero.scripts.run_train` etc.; configuration in `alpha_zero/config.py`
with overrides from `alpha_zero/.env`, `ALPHA_<SECTION>_<FIELD>` naming).
The training code is intentionally untracked - git carries only the model
definitions, configs, and inference modules the bot needs to play from a
checkpoint - so these commands are available only on a training machine.
