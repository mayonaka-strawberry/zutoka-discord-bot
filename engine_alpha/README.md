# engine_alpha

The ZUTOMAYO CARD rules engine. It is a deterministic state machine that can be cloned at any decision point, so search can branch on every choice. It imports nothing from `zutomayo` and only reads `zutomayo/data/cards.json`.

## Design

- **API:** `Game(seed, mode="draft", decks=None, max_turns=200, night_player=None)` with `decision_context()`, `legal_actions()`, `apply(action)`, `clone()`, `is_terminal()` and `returns()`.
- **Modes:** `"draft"` opens with a 40-ply alternating draft from the full card pool; `"fixed_decks"` takes `decks`.
- **Phases:** a 14-phase driver in `game.py`. Mid-effect state lives in explicit frames rather than coroutines, which is what makes cloning possible.
- **Decisions:** 4 `DecisionRequest` kinds (select card, select identity, select number, binary), each tagged with a purpose. Every answer is a small int.
- **Effects:** the IR (`effects/catalog_data.py`) has 253 entries:
  - 250 are run by the interpreter, 8 of them through custom step-machine handlers.
  - 3 are applied inline by the rules (02-005, 02-007, 02-062).
  - 2 reduce costs and resolve first in their owner's batch (02-006, 04-065).

  `effects/features.py` derives each effect's 160-dim feature vector from the IR, so features cannot drift from behavior.
- **Chance:** a counter-based RNG. The state stores only `(rng_key, rng_ctr)`, so clones share every future. `derive_seed` gives each TCG game its own seed. The first draw sets the day/night sides; `night_player` overrides the result, but the draw still happens.
- **Events:** a `GameState` may carry an `event_sink` list that collects observation-only tuples (`events.py`) for narration. `fast_clone` detaches it, so search clones stay silent.
- **Observation:** `encoding/observation.py` encodes a state as 172 tokens for the model stacks. It includes hidden information (opponent hand, deck order) on purpose.

## Rulings

The sources are the official Q&A (<https://zutomayocard.net/qa/>), Ground Rules ver 1.0.1, and the Japanese card text in `cards.json`. Each row has a test in `tests/test_rulings.py` that cites its source.

| Ruling | Source | Code |
| --- | --- | --- |
| Attack modifiers apply in resolution order, clamped to >= 0 after each step. | Q&A No.54, 68, 82 | `battle.get_effective_attack` |
| An unmet power cost makes the final attack 0, even after a 04-099 set. | Ground Rules 2.3.6, 5.1.3.2; Q&A No.40, 73 | `battle.get_effective_attack` |
| 03-064 adds each player's own HP to their attack at attack determination. | Q&A No.33 | `state.ATTACK_MOD_ADD_OWN_HP` |
| A day/night crossing counts if it happened at any step this turn. A rewind is not a crossing. | Q&A No.17, 18 | `effects/conditions.py`, `battle.set_chronos` |
| Slot A cannot be passed while the hand has cards; slot B can. | Ground Rules 5.2.1.5; Q&A No.4 | `game.py` |
| The game ends the instant HP reaches 0, and pending effects are dropped. | Ground Rules 1.2.3, 5.4.1; Q&A No.41 | `battle.record_hp_zero` |
| Double knock-out: the first player to reach 0 HP loses. HP never produces a draw. | House ruling | `battle.record_hp_zero` |
| If an effect must move more cards out of a deck than it holds, the deck's owner loses. Peeks and reorders (03-097, 03-103, 04-088) move none out. | Ground Rules 8.2.1, 8.2.2; Q&A No.45, 70 | `effects/interpreter.py` |
| A deck reaching 0 is not a loss until the next mandatory draw. | Q&A No.92 | `game.py` |
| If neither player can make the end-of-turn draw, the game is a draw. | Ground Rules 5.4.3.1 | `game._ph_end_turn` |
| Public-zone selections take at least 1 card; hidden-zone selections may take 0. | Ground Rules 1.3.5.1; Q&A No.79, 90 | `effects/custom.py` |
| A 04-002 chain cannot re-select a card that is already resolving above it. | Q&A No.79, 83 | `custom.shade_use_two` |
| 04-008, 04-032 and 04-097 always reveal the hand when their cost is met; only the bonus is conditional. | Q&A No.89 | `effects/catalog_data.py` |
| 03-058, 03-085 and 04-091 leave play the moment their damage condition is met. | Ground Rules 6.1.3.5; Q&A No.12, 16, 80 | `battle.check_damage_triggered_removal` |
| Each copy of 03-058 heals separately. | Q&A No.26 | `effects/turn_end.py` |
| At turn end, the priority player's batch resolves first, and each player orders their own effects. | Ground Rules 5.2.10.2, 10.2.4; Q&A No.96, 102 | `game._ph_turn_end_effects` |
| 03-027's turn-end damage belongs to its caster. | Q&A No.25 | `effects/turn_end.py` |
| 03-097 returns the revealed card to the top of the deck; if that card costs 6 or more, 03-097 moves itself to the charger. | Q&A No.45 | `custom.reveal_top_03_097` |
| All three 03-055 block terminations hold. | Q&A No.28 | `effects/removal.py` |

Citation trap: the 04-099 ruling rests on Ground Rules 2.3.6 and 5.1.3.2. It does not rest on Ground Rules 7.1.2, which covers added attack only, or on Q&A No.82, which covers resolution order only.

## Changing the effect catalog

- `effect_features` is a non-persistent model buffer rebuilt from the live catalog. A strict checkpoint load therefore does not prove the catalog is unchanged, and a changed effect row is off-policy drift for deployed models.
- Run `tests/test_defect_sweep.py`. It forces decks per defect group, because a random deck exposes any one card in only about 2.4% of games. It also encodes the observation at every decision and caps chain depth, so a hang fails instead of hanging.

## Layout

```
cards.py        card database (425 cards), vocabularies, lookup arrays
rng.py          counter-based RNG (splitmix64 + Fisher-Yates)
state.py        GameState, PlayerState, Frame; fast_clone
actions.py      DecisionRequest kinds and purposes
events.py       event constants and tuple layouts
zones.py        placement triggers
battle.py       attack, battle resolution, win checks
game.py         phase driver (Game)
draft.py        draft legality
baselines.py    RandomAgent, GreedyHeuristicAgent
config.py       EngineConfig: records the engine's inline constants for training runs
effects/        IR, interpreter, conditions, selectors, catalog, custom handlers, removal, turn end, features
encoding/       observation encoder
tests/          card, invariant, ruling, event, defect-sweep and playout tests
scripts/        bench_engine, fuzz, transcript
```

## Commands

Run from the repository root:

```
python -m pytest engine_alpha/tests -q
python -m engine_alpha.scripts.bench_engine            # games/s and clone latency
python -m engine_alpha.scripts.fuzz --steps 1000000    # random games checking invariants
python -m engine_alpha.scripts.transcript --seed 11    # add --draft for a draft game
```
