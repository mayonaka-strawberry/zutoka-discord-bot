# Zutoka Discord Bot

A Discord bot implementing the ZUTOMAYO CARD trading card game: 2-player
matches, solo matches against a reinforcement-learning bot, and best-of-N TCG
series with side decks. Rules follow the official game
(https://zutomayocard.net/start-guide/, rule guide PDF, errata, and Q&A pages).

## Setup

The bot requires PostgreSQL: all player data (profiles, decks, display names,
game records, decision logs, game events) lives in a PostgreSQL database.
Card definitions stay in `zutomayo/data/cards.json`. Follow
[docs/postgresql_setup.md](docs/postgresql_setup.md) for a from-scratch
install on Windows, macOS, or Linux, database/role creation, and the one-time
cutover from the old JSON storage (`scripts/migrate_json_to_postgresql.py`
migrates decks, TCG decks, and display names; player statistics start fresh).

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install -r requirements-dev.txt   # test tooling
```

Create a `.env` file at the repository root containing:

```
DISCORD_TOKEN=<bot token>
DATABASE_URL=postgresql://zutoka_bot:<password>@localhost:5432/zutoka
ZUTOKA_TEST_DATABASE_URL=postgresql://zutoka_bot:<password>@localhost:5432/zutoka_test   # optional, integration tests
```

(the local token is the development bot; production runs as a separate app),
then start the bot with:

```
python main.py
```

The schema is applied automatically at startup (or manually with
`python scripts/apply_schema.py`). The database lives in the PostgreSQL
server, not in this repository — back it up or move it between machines with
the export/import scripts described in
[docs/postgresql_setup.md](docs/postgresql_setup.md) (`dump_database.py` /
`restore_database.py` for binary backups, `export_database.py` /
`import_database.py` for portable JSON transfers).

## Commands

All slash commands live under `/zutomayo`:

- Games: `create`, `createtcg`, `join`, `playuniguri`, `playunigurieasy`,
  `quit`, `end` (ends a live game, or abandons one of your saved games with a
  forfeit), `saveandquit`, `resume`.
- Game records: `summary` (full replay of a finished game — phases, every
  decision, effect priority and order, hands, battle results, TCG side-deck
  swaps; searchable by game id), `history` (recent finished games for you or
  a searched player — the easy way to find a game id).
- Decks: `makedeck`, `viewdeck`, `managedecks` and their TCG twins
  (`makedecktcg`, `viewdecktcg`, `managedeckstcg`). View/manage take a deck
  name with autocomplete search; the edit modal is pre-filled with the deck's
  current card list.
- Players: `profilestats` (your own, or search another player by name — never
  pings), `leaderboard`, `leaderboardtcg`, `editname`.
- Extras: `gacha`, `gachabox`, `ranksongs`.

Game ids are `YYYYMMDD-NNNNN` (UTC date plus a daily counter starting at
00000). Saving a game keeps no partial results; resuming a 2-player game
requires both players to confirm and replays the game deterministically from
its decision log, so saved games are best-effort across bot updates (a
diverged game is marked unrecoverable, but its summary keeps working).

## Architecture

- `zutomayo/models/` — plain dataclasses: `Card` (immutable catalog entry),
  `CardInstance` (a card in play), `Player`, `GameState`.
- `zutomayo/data/database.py` + `schema.sql` — the asyncpg connection pool
  (created in `setup_hook`, JSONB codecs installed per connection) and the
  idempotent schema. Every storage module exposes a swappable `backend`
  attribute; tests install in-memory fakes (`tests/fakes.py`).
- `zutomayo/engine/` — game orchestration:
  - `game_session.py`: per-game `GameSession` (players, seeded RNG, runtime
    slots) and the global `session_manager`. Game ids come from
    `zutomayo/data/game_id_allocator.py` (atomic per-day counter in
    PostgreSQL).
  - `game_flow.py`: the full match driver (setup, redraw, turns, battle,
    game end). `solo_game_flow.py` only adds agent construction and the bot's
    deck selection; `tcg_match_flow.py` wraps matches into a best-of-N series
    with a card-switching phase.
  - `decisions.py` / `decision_broker.py`: every interactive choice is a
    `DecisionRequest` answered through the `DecisionBroker`. Adapters answer
    for each player: `adapters/discord_adapter.py` renders the Discord views,
    `adapters/bot_agent_adapter.py` asks the solo bot agent.
  - `match_transport.py`: all outgoing messages flow through a
    `MatchTransport` (Discord DMs and channel sends, or test recorders);
    channel narration is mirrored into the game event stream.
  - `game_persistence.py` / `resume_manager.py`: every game owns a permanent
    record in PostgreSQL — a manifest (identity, RNG seed, deck lists), an
    append-only decision log, and a live event stream (`game_events.py`:
    every phase, decision, day/night effect priority, effect resolution
    order, hands, battle results, state snapshots). Lifecycle is tracked by
    status (active, saved, completed, quit, abandoned, divergence_failed);
    nothing is deleted. On startup the bot deterministically replays active
    games from their logs (transport muted) and continues them live, so games
    survive restarts; `/zutomayo resume` runs the same machinery on demand
    for saved games. Replays that no longer match the log (after a code
    change) end the game gracefully with no recorded result.
  - `turn_manager.py`: mechanical rules (chronos, swaps, battle, end turn).
  - `bot_agent.py` + `rl_model_v2.py` + `uniguri_env_v2.py`: the solo
    opponent (メカうにぐり), driven by trained V2 PyTorch checkpoints. The
    headless training stack needs no database; the generated deck pools
    (`bot_decks.json`, `best_decks_v2*.json`, `default_decks.json`) stay as
    files.
- `zutomayo/effects/` — three layers:
  - `effect_engine.py`: effect collection and ordering, the cost gate
    (`is_effect_affordable`), the declarative `_AREA_ENCHANT_REMOVAL_RULES`
    table, end-of-turn processing, and the state-mutation primitives every
    handler routes through (`deal_damage`, `heal`, `lose_game`,
    `place_in_abyss`, `place_in_power_charger`, `return_to_deck_bottom`,
    `return_to_deck_top`, `mill_deck_top_to_abyss`, `broadcast_reveal`).
  - `card_effect_helpers.py`: shared effect templates (attribute/day-night
    buffs, zone scans, reveal and hand-placement flows) plus the narration
    builders `announce_effect_outcome` / `announce_effect_fizzle` that give
    all packs one player-facing message grammar.
  - `effects/cards/effect_XX_YYY.py`: one module per card effect (250
    modules, 247 registered handlers — 02-005/02-007/02-062 live inside the
    engine). Cards that share a shape are thin wrappers over a template;
    cards with unique text keep bespoke handlers.
- `zutomayo/data/` — card catalog loading (cached), deck persistence
  (`deck_repository.py` serves both the standard and TCG formats over the
  `decks` / `decks_tcg` tables), validators, player profiles with Elo and
  per-game `elo_history` rows, display-name storage (write-through cache),
  gacha.
- `zutomayo/ui/` — embeds, the PIL board renderer (run off-thread), the
  interactive Discord views, the game summary renderer
  (`game_summary_view.py`), and the resume confirmation view.

Determinism contract: all game randomness (coin flip, shuffles, the four
shuffling effects) draws from the session's seeded generator, and every player
decision is logged by sequence number, which is what makes restart replay,
`/zutomayo resume`, and the transcript regression suites possible. Event
recording is observation-only and suppressed during replay, so it can never
affect game behavior.

## Tests and verification

```
python tests/run_all.py                          # everything: pytest + coverage gates + transcript compares
python -m pytest tests/ -q                       # unit and characterization suite
python tests/run_engine_regression.py compare    # Tier A: 1528 seeded headless engine games vs baselines
python tests/run_flow_regression.py compare      # Tier B: full flow matches (2-player, solo, TCG) vs baselines
```

The suite runs without a database (in-memory backends are installed by
`tests/conftest.py`). PostgreSQL integration tests run additionally when
`ZUTOKA_TEST_DATABASE_URL` is set.

Baselines live in `tests/baselines/` as gzip JSONL transcripts. Regenerate
with `write` instead of `compare` only when a behavior change is intended;
the most recent intended regeneration was the effect-narration unification
(packs 01-04 now share one narration grammar emitted by the template layer).
The effect tests are characterization tests: they pin current behavior, which
has been verified against the official rules; do not "fix" rules in tests.

## Formats

- Standard: 20-card deck, up to 2 copies per card.
- TCG (`/zutomayo createtcg`): best-of-3 or 5 with a 20-card main deck plus an
  8-card side deck; between matches both players may swap cards between main
  and side decks. Per-match stats are tracked separately; the TCG Elo ladder
  moves once per completed series.
