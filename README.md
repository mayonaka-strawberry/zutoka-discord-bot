# Zutoka Discord Bot

A Discord bot implementing the ZUTOMAYO CARD trading card game. Rules follow the official game
(https://zutomayocard.net/start-guide/).

## Setup

Developed and tested on Python 3.14. All dependencies are pinned in
`requirements.txt` (`numpy` is a direct engine dependency; `torch` is only
exercised when a solo model opponent is deployed, but is installed with the
rest).

The bot requires PostgreSQL: all player data (profiles, decks, display names,
game records, decision logs, game events) lives in a PostgreSQL database.
Card definitions stay in `zutomayo/data/cards.json`. Follow
[docs/postgresql_setup.md](docs/postgresql_setup.md) for a from-scratch
install on Windows, macOS, or Linux, database/role creation, and backup and
transfer tooling.

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

Create a `.env` file at the repository root containing:

| Key | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | Bot token. The local token is the development bot; production runs as a separate app. |
| `DATABASE_URL` | e.g. `postgresql://zutoka_bot:<password>@localhost:5432/zutoka` |
| `ZUTOKA_TEST_DATABASE_URL` | Optional; enables the PostgreSQL integration tests against a scratch database. |

then start the bot with:

```
python main.py
```

`main.py` initializes the connection pool, applies the schema (idempotent;
also available manually as `python scripts/apply_schema.py`), warms the
display-name cache, loads the command cog, syncs the command tree, and
resumes any games that were live when the bot last stopped. Only
non-privileged intents are used. Back up or move the database with the
scripts described in [Scripts and admin tooling](#scripts-and-admin-tooling).

## Commands

All slash commands live under `/zutomayo` (12 top-level entries: 11 commands
plus the `deck` subgroup):

| Command | Parameters | Notes |
| --- | --- | --- |
| `create` | `format`: Standard (default) \| TCG (best of N) · `deck`: Saved (default) \| Draft · `opponent`: player name (default `player`) · `best_of`: 3 \| 5 (TCG) · `boxes`: 1-5 (draft) · `visibility`: Public \| Private (draft) | One command for every game mode. The `opponent` autocomplete lists trained model opponents alongside `player` once a checkpoint is deployed; picking one starts a solo game in DMs. |
| `join <game_id>` | | Join a created game. Guild-only. |
| `end <game_id>` | autocomplete | End an active game. Guild-only. |
| `quit` | `save`: True \| False (default False) | Leave your active game; `save: True` replaces the old saveandquit. |
| `resume <game_id>` | autocomplete | Resume a saved game. Two-player resumes need both players to confirm. |
| `deck make` | `name`, `format`: Standard \| TCG | Opens the deck builder. |
| `deck view` | `deck` (autocomplete), `format` | Shows a saved deck. |
| `deck manage` | `deck` (autocomplete), `format` | Rename, edit, or delete a saved deck. |
| `gacha` | `pack`: 1-4 (required) · `amount`: Pack (default) \| Box | One pack of 5 cards or a box of 10 packs from the chosen expansion pack. |
| `summary <game_id>` | autocomplete | Full replay of a finished game, from the permanent event stream. |
| `history` | `player` (optional, autocomplete) | Recent game results. |
| `profilestats` | `player` (optional, autocomplete) | Profile with Elo, win/loss, per-deck and per-opponent stats. |
| `editname` | `name` (optional) | Sets a display name; omitting it reverts to your Discord name. |
| `leaderboard` | `format`: Standard (default) \| TCG | Elo ladder for the chosen format. |

Game ids are `YYYYMMDD-XXXXX` (UTC date plus a daily counter, allocated
atomically in the database). Saving a game keeps no partial results; resuming
replays the game deterministically from its decision log, so saved games are
best-effort across bot updates (a diverged game is marked unrecoverable, but
its summary keeps working).

In-game decision prompts time out after 300 seconds (the TCG side-deck switch
allows 750) and resolve to a deterministic fallback action; three consecutive
timeouts forfeit the game.

## Formats

- Standard: 20-card deck, up to 2 copies per card.
- TCG: best-of-3 or 5 with a 20-card main deck plus an 8-card side deck;
  between matches both players may swap cards between main and side decks.
  Per-match stats are tracked separately; the TCG Elo ladder moves once per
  completed series.
- Draft: sealed variants of both formats. Each player opens gacha boxes
  (1-5, 50 cards per box) and builds a deck only from the opened cards
  through a paginated menu in DM - 20 picks for Standard, 28 for TCG (of
  which 8 then become the side deck), at most 2 copies per card. Downstream
  play, Elo, and leaderboards are identical to the non-draft formats.
- Solo: a standard game against a trained model opponent in DMs, available
  once a checkpoint from `alpha_zero/` or `ppo_transformer/` is deployed.

Playing a CHAOS bank-or-lose card (`04-006`, `04-027`, `04-028`, `04-088`)
without the abyss cards to pay for it loses the game on the spot, which makes it
the cheapest way to hand an opponent a win. When that happens on turn 1 of a
standard game, the player who did it takes their full Elo loss but the winner
gains no Elo. Win/loss records, deck stats and opponent stats are unaffected and
count the game normally for both players.

## Architecture

### engine_alpha - the rules engine

The game rules run on **engine_alpha** (see
[engine_alpha/README.md](engine_alpha/README.md)): a self-contained,
deterministic state machine that imports nothing from the `zutomayo` package
(it only reads `cards.json` as data). Its public surface is
`Game(seed, mode, decks)` with
`decision_context() / legal_actions() / apply(action) / clone() /
is_terminal() / returns()`:

- An explicit 14-phase driver (draft, mulligan, initial set/reveal, set
  cards, reveal, chronos, character/area swap, effects, battle, turn end,
  game over) - no suspended coroutines, so the game is clonable at every
  decision point.
- Every interactive choice is one of 4 `DecisionRequest` kinds (select card,
  select identity, select number, binary) tagged with a purpose, and every
  answer is a small int action.
- All 253 card effects are declarative IR (`effects/catalog_data.py`)
  executed by a micro-step interpreter: 250 dispatchable programs, 3
  engine-inline passives, 8 custom step-machine handlers for effects that do
  not fit linear IR, and 2 cost-reducing effects with forced-first ordering.
  The card database holds 425 cards.
- Chance is a counter-based RNG: the state stores only `(rng_key, rng_ctr)`,
  so clones agree on all futures, and `derive_seed` gives each game of a
  TCG series its own seed from one persisted series seed.
- A `GameState` may carry an observation-only event sink (draws, placements,
  battle results, phase changes, ...) that external drivers narrate;
  `fast_clone` detaches it so search clones are silent.
- `encoding/observation.py` encodes a state into the 172-token observation
  the model stacks consume.

### zutomayo/match - the match runtime

- `decisions.py` / `broker.py`: every interactive choice is a
  `MatchDecisionRequest` answered through the `MatchDecisionBroker`. The
  broker assigns each request a sequence number, verifies submissions for
  legality before accepting them, and appends the response to the decision
  log; during replay it answers requests from the log instead, verifying a
  fingerprint of each request against what was recorded. Timeouts resolve to
  a deterministic fallback action (pass when legal, otherwise the lowest
  legal action); three consecutive timeouts forfeit.
- `presentation.py` / `discord_adapter.py`: maps engine decisions onto the
  Discord views; the mulligan and set-cards prompts are compound (one view
  answers the engine's iterative requests, both players prompted
  concurrently).
- `state_view.py`: read-only `BoardView` / `PlayerView` / `CardView`
  projections consumed by embeds and the PIL board renderer.
- `narrator.py`: translates engine events (`engine_alpha/events.py`) into
  channel/DM messages and the permanent `game_events` stream that powers
  `/zutomayo summary`.
- `transport.py`: the `MatchTransport` protocol all outgoing messages route
  through - Discord DMs live, a recorder in tests, or muted during replay.
- `match_driver.py` / `match_flow.py` / `series_flow.py` / `draft_flow.py` /
  `solo_flow.py`: the driver loop and the mode orchestrators (single match,
  TCG best-of-N with side-deck switching and one globally sequenced record
  for the whole series, gacha-box draft, solo versus a model opponent).
- `persistence.py` / `resume.py`: every game owns a permanent PostgreSQL
  record (a manifest with the engine seed and pre-shuffle decks, an
  append-only int-action decision log, the event stream). On startup, active
  games are rebuilt from their manifests and replayed from their logs with
  the transport muted, then go live and continue; `/zutomayo resume` runs the
  same machinery on demand. A fingerprint mismatch during replay marks the
  game unrecoverable and announces it.
- `agents/`: the solo opponents. `available_solo_opponents()` includes a
  model stack only when its `find_checkpoint()` locates a deployed
  checkpoint, so solo choices appear in `/zutomayo create` only when one
  exists. Inference runs off the event loop with a watchdog; any agent
  failure submits a legal fallback action rather than hanging the game.

### The rest of the bot

- `zutomayo/cogs/game_cog.py` - the discord.py cog implementing every
  `/zutomayo` command, autocomplete, and the glue into the match flows.
- `zutomayo/engine/` - session bookkeeping (`game_session.py`), the
  PostgreSQL game-record backend (`game_persistence.py`), the event
  taxonomy (`game_events.py`).
- `zutomayo/data/` - the storage layer, all asyncpg: card catalog loading
  (`card_loader.py`, cached), connection pool and schema (`database.py`,
  `schema.sql`), deck persistence (`deck_repository.py` with the
  `deck_storage.py` / `deck_storage_tcg.py` facades), deck validation
  (`deck_validator.py` / `deck_validator_tcg.py`), player profiles with Elo
  (`player_storage.py`), display names (`name_storage.py`), gacha draws
  (`gacha.py`), and atomic game-id allocation (`game_id_allocator.py`).
- `zutomayo/ui/` - embeds, the PIL board renderer (run off-thread), the
  interactive Discord views (deck builder/management, draft picking, TCG
  side-deck switch, resume confirmation, leaderboard), and the game summary
  renderer.
- `zutomayo/enums/`, `zutomayo/models/`, `zutomayo/utils/` - card
  attribute/type/rarity/song enums, the `Card` model, and small Discord
  helpers.

### Model stacks

`alpha_zero/` and `ppo_transformer/` are the model training stacks. Their
training code is intentionally untracked; git carries only what the bot
needs to play from a checkpoint (model definitions, configs, and the
inference modules). Checkpoints are discovered at `<stack>/deploy/model.pt`
first, falling back to the newest checkpoint under `<stack>/runs/`.
`model_common/device.py` picks CUDA, then Apple Silicon MPS, then CPU at
runtime, and caps inference threads so the Discord event loop is never
starved.

## Determinism contract

The engine seed drives the coin flip and every shuffle through the
counter-based RNG, and every decision is one logged int applied in sequence -
that is what makes restart replay, `/zutomayo resume`, and the transcript
regression suite possible. Event recording is observation-only and suppressed
during replay. The one deliberate exception is the draft deck-building phase:
box opening happens before the game record exists and is not part of the
replayed log - only the resulting decks are persisted in the manifest.

## Scripts and admin tooling

All scripts run from the repository root and read `.env` for the database
URL unless noted.

| Script | Purpose |
| --- | --- |
| `scripts/apply_schema.py` | Apply the PostgreSQL schema manually (idempotent). |
| `scripts/export_database.py` / `scripts/import_database.py` | Portable JSON dump/load of all tables; import upserts idempotently and supports `--replace` and `--dry-run`. |
| `scripts/dump_database.py` / `scripts/restore_database.py` | Binary backup pair using `pg_dump`/`pg_restore` (custom format). |
| `scripts/postgresql_tools.py` | Locates the PostgreSQL client binaries across platforms (`PGBIN`, PATH, defaults); used by the dump/restore pair. |
| `scripts/database_transfer.py` | Shared table specifications and serializers for the JSON export/import pair. |
| `scripts/reset_elo.py` | Reset one Elo ladder for all players: `--format standard` or `--format tcg`. Lifetime and deck stats are untouched. |
| `scripts/migrate_json_to_postgresql.py` | One-shot migration of the legacy JSON decks/usernames into PostgreSQL (already run). |
| `scripts/wipe_legacy_game_records.py` | One-shot cutover: clears pre-engine_alpha game records and Elo, preserving decks and names (already run). |
| `scripts/calibrate_board.py` | Draws colored zone markers onto the board image to verify renderer coordinates. |
| `scripts/populate_card_images.py` / `scripts/remove_corners.py` | Card-image asset tooling (fill the `image` field in `cards.json`; mask white corners). |

## Tests and verification

```
python tests/run_all.py                          # everything: pytest + coverage gates + transcript compare
python -m pytest engine_alpha/tests tests -q     # unit suite
python tests/run_match_regression.py compare     # 24 seeded full-stack games vs golden transcripts
python -m engine_alpha.scripts.fuzz              # engine invariant fuzzer
python -m engine_alpha.scripts.bench_engine      # engine performance gate
python -m engine_alpha.scripts.transcript --seed 11   # human-readable single game
```

The suite runs without a database: an autouse fixture in `tests/conftest.py`
swaps every storage backend for in-memory fakes (`tests/fakes.py`).
PostgreSQL integration tests under `tests/data/` run additionally when
`ZUTOKA_TEST_DATABASE_URL` is set. Note that the test suite does not read
`.env` - the variable must be present in the shell environment - and the
data-layer coverage gate below only passes when the integration tests run.

`tests/run_all.py` enforces per-area coverage gates on top of the full
pytest run: engine_alpha core 84%, match layer 90%, data layer 87%, ui core
(embeds, renderers) 80%. Cogs, Discord views and flows, resume glue, and the
model stacks are report-only; their guarantee is the transcript tier plus
dev-bot playtests. Thresholds are measured-minus-flake-margin: raise them,
never lower them.

The match regression suite drives 24 seeded games through the real match
runtime (broker, presentation, narrator, driver) with scripted players and a
recording transport, and compares winners, decision fingerprints and
payloads, events, channel messages, and a final state digest against the
golden baseline in `tests/baselines/match/match_games.jsonl.gz`. Regenerate
with `write` instead of `compare` only when a behavior change is intended.
