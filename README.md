# Zutoka Discord Bot

A Discord bot implementing the ZUTOMAYO CARD trading card game: 2-player
matches, solo matches against a reinforcement-learning bot, and best-of-N TCG
series with side decks. Rules follow the official game
(https://zutomayocard.net/start-guide/, rule guide PDF, errata, and Q&A pages).

## Setup

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install -r requirements-dev.txt   # test tooling
```

Create a `.env` file at the repository root containing `DISCORD_TOKEN=<bot token>`
(the local token is the development bot; production runs as a separate app),
then start the bot with:

```
python main.py
```

All slash commands live under `/zutomayo` (create, join, playuniguri,
createtcg, makedeck, viewdeck, managedecks and their TCG twins, gacha,
leaderboard, profilestats, editname, end, quit, ranksongs).

## Architecture

- `zutomayo/models/` — plain dataclasses: `Card` (immutable catalog entry),
  `CardInstance` (a card in play), `Player`, `GameState`.
- `zutomayo/engine/` — game orchestration:
  - `game_session.py`: per-game `GameSession` (players, seeded RNG, runtime
    slots) and the global `session_manager`.
  - `game_flow.py`: the full match driver (setup, redraw, turns, battle,
    game end). `solo_game_flow.py` only adds agent construction and the bot's
    deck selection; `tcg_match_flow.py` wraps matches into a best-of-N series
    with a card-switching phase.
  - `decisions.py` / `decision_broker.py`: every interactive choice is a
    `DecisionRequest` answered through the `DecisionBroker`. Adapters answer
    for each player: `adapters/discord_adapter.py` renders the Discord views,
    `adapters/bot_agent_adapter.py` asks the solo bot agent.
  - `match_transport.py`: all outgoing messages flow through a
    `MatchTransport` (Discord DMs and channel sends, or test recorders).
  - `game_persistence.py` / `resume_manager.py`: every match writes a manifest
    (identity, RNG seed, deck lists) and an append-only decision log under
    `zutomayo/active_games/<game_id>/`. On startup the bot deterministically
    replays in-flight games from those logs (transport muted) and continues
    them live, so games survive restarts. Replays that no longer match the
    log (after a code change) end the game gracefully with no recorded result.
  - `turn_manager.py`: mechanical rules (chronos, swaps, battle, end turn).
  - `bot_agent.py` + `rl_model_v2.py` + `uniguri_env_v2.py`: the solo
    opponent (メカうにぐり), driven by trained V2 PyTorch checkpoints.
- `zutomayo/effects/` — `effect_engine.py` (effect collection, ordering, cost
  gate, area-enchant removal rules, end-of-turn processing) and
  `effects/cards/effect_XX_YYY.py`, one module per card effect (252 handlers).
- `zutomayo/data/` — card catalog loading (cached), deck persistence
  (`deck_repository.py` serves both the standard and TCG formats), validators,
  player profiles and Elo, display-name storage, gacha.
- `zutomayo/ui/` — embeds, the PIL board renderer (run off-thread), and the
  interactive Discord views.

Determinism contract: all game randomness (coin flip, shuffles, the four
shuffling effects) draws from the session's seeded generator, and every player
decision is logged by sequence number, which is what makes restart replay and
the transcript regression suites possible.

## Tests and verification

```
python tests/run_all.py                          # everything: pytest + coverage gates + transcript compares
python -m pytest tests/ -q                       # unit and characterization suite
python tests/run_engine_regression.py compare    # Tier A: 1528 seeded headless engine games vs baselines
python tests/run_flow_regression.py compare      # Tier B: full flow matches (2-player, solo, TCG) vs baselines
```

Baselines live in `tests/baselines/` as gzip JSONL transcripts. Regenerate
with `write` instead of `compare` only when a behavior change is intended.
The effect tests are characterization tests: they pin current behavior, which
has been verified against the official rules; do not "fix" rules in tests.

## Formats

- Standard: 20-card deck, up to 2 copies per card.
- TCG (`/zutomayo createtcg`): best-of-3 or 5 with a 20-card main deck plus an
  8-card side deck; between matches both players may swap cards between main
  and side decks. Per-match stats are tracked separately; the TCG Elo ladder
  moves once per completed series.
