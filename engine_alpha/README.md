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
  placements, battle results, phase changes, ...) for external drivers to
  narrate. `fast_clone` always detaches the sink, so search clones are
  silent and pay only a `None` check.
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

## Layout

```
config.py              EngineConfig constants (documentary; engine uses inline values)
cards.py               card DB: 422 CardDefs, dense vocabularies, flat lookup arrays
rng.py                 counter-based deterministic RNG (splitmix64 Fisher-Yates)
state.py               GameState/PlayerState/Frame: __slots__, ints, fast_clone
actions.py             the 4 DecisionRequest kinds + purpose tags
events.py              observation-only engine event constants and tuple layouts
zones.py               placement triggers (agent-based vs location-based semantics)
battle.py              attack precedence chain, battle resolution, win checks
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
