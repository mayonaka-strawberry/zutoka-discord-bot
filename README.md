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

All slash commands live under `/zutomayo` (15 top-level entries: 14 commands
plus the `deck` subgroup):

| Command | Parameters | Notes |
| --- | --- | --- |
| `create` | `format`: Standard (default) \| TCG · `best_of`: 3 \| 5 (TCG) | A game against another player, using saved or built decks. Guild-only. |
| `createdraft` | `boxes`: 1-5 · `visibility`: Public (default) \| Private · `format`: Standard (default) \| TCG · `best_of`: 3 \| 5 (TCG) | Same, but both players draft from gacha boxes they open. `boxes` defaults to 2 (3 for TCG). Guild-only. |
| `playuniguri` | `model`: A \| B (required) | A solo game against a trained model opponent, in DMs. A is the AlphaZero stack, B the PPO transformer; a model that has no deployed checkpoint says so rather than starting a game. |
| `join <game_id>` | | Join a created game. Guild-only. |
| `end <game_id>` | autocomplete | End an active game. Guild-only. |
| `quit` | `save`: True \| False (default False) | Leave your active game; `save: True` replaces the old saveandquit. |
| `resume <game_id>` | autocomplete | Resume a saved game, from a DM or a server channel. Two-player resumes need both players to confirm; asked for from a DM, the request is delivered to the opponent's DM. |
| `deck make` | `name`, `format`: Standard \| TCG | Opens the deck builder. |
| `deck view` | `deck` (autocomplete), `format` | Shows a saved deck. |
| `deck manage` | `deck` (autocomplete), `format` | Rename, edit, or delete a saved deck. |
| `gacha` | `pack`: 1-4 (required) | One pack of 5 cards from the chosen expansion pack. |
| `gachabox` | `pack`: 1-4 (required) | A box of 10 packs from the chosen expansion pack. |
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

Every game is played out in DMs and the channel only carries public narration,
so a saved game can be resumed from anywhere. Resuming a two-player game from a
server channel moves its narration to that channel; resuming from a DM leaves
the game on the channel it was already on. Solo games are recorded with no
channel at all, so nothing about them is ever posted publicly.

In-game decision prompts time out after 300 seconds (the TCG side-deck switch
allows 750) and resolve to a deterministic fallback action; three consecutive
timeouts forfeit the game.

## Formats

- Standard: 20-card deck, up to 2 copies per card.
- TCG: best-of-3 or 5 with a 20-card main deck plus an 8-card side deck;
  between matches both players may swap cards between main and side decks,
  and then the player who lost the match chooses the day/night side they play
  in the next one (match 1 is a coin flip, and a drawn match replays without
  changing who chooses). Per-match stats are tracked separately; the TCG Elo
  ladder moves once per completed series.
- Draft: sealed variants of both formats. Each player opens gacha boxes
  (1-5, 50 cards per box) and builds a deck only from the opened cards
  through a paginated menu in DM - 20 picks for Standard, 28 for TCG (of
  which 8 then become the side deck), at most 2 copies per card. Downstream
  play, Elo, and leaderboards are identical to the non-draft formats.
- Solo: a standard game against a trained model opponent in DMs, started with
  `/zutomayo playuniguri`. Model A is the `alpha_zero/` stack and model B the
  `ppo_transformer/` one; each is playable once its checkpoint is deployed
  (see [Solo opponents](#solo-opponents)).

Playing a CHAOS bank-or-lose card (`04-006`, `04-027`, `04-028`, `04-088`)
without the abyss cards to pay for it loses the game on the spot, which makes it
the cheapest way to hand an opponent a win. When that happens on turn 1 of a
standard game, the winner gains no Elo and the player who did it takes a heavy
Elo penalty - well beyond an ordinary loss, though it can never drive a rating
below 0. Win/loss records, deck stats and opponent stats are unaffected and count
the game normally for both players. The penalty is applied silently: nothing is
posted to the channel or sent by DM, so the exact figures are deliberately not
documented here.

## Architecture

### engine_alpha - the rules engine

The game rules run on **engine_alpha** (see
[engine_alpha/README.md](engine_alpha/README.md)): a self-contained,
deterministic state machine that imports nothing from the `zutomayo` package
(it only reads `cards.json` as data). Its public surface is
`Game(seed, mode, decks, night_player=None)` with
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
  TCG series its own seed from one persisted series seed. The first draw
  picks the day/night sides; `night_player` overrides it without skipping
  the draw, which is how a TCG loser's side choice reaches the engine.
- A `GameState` may carry an observation-only event sink (draws, placements,
  battle results, phase changes, ...) that external drivers narrate;
  `fast_clone` detaches it so search clones are silent.
- `encoding/observation.py` encodes a state into the 172-token observation
  the model stacks consume.
- The rules were audited end to end on 2026-08-13 against the official Q&A
  (all 104 entries), Ground Rules ver 1.0.1, and the Japanese card text, and
  eleven divergences were fixed. An independent review on 2026-08-14 then found
  that the rework had introduced defects of its own -- including a regression and
  two crashes -- which are also fixed. `engine_alpha/README.md` lists every item
  with its source; the ruling tests in `engine_alpha/tests/test_rulings.py` cite
  the Q&A number or Ground Rule section they enforce, and
  `engine_alpha/tests/test_defect_sweep.py` is the deck-forced gate that catches
  the classes the ordinary suite cannot see.

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
  Discord views; the mulligan, initial-card and set-cards prompts are compound
  (one view answers the engine's iterative requests, both players prompted
  concurrently). Concurrency is also what keeps a placement secret: neither
  player learns anything from the other's timing, and nothing is posted between
  the two commitments.
- `state_view.py`: read-only `BoardView` / `PlayerView` / `CardView`
  projections consumed by embeds and the PIL board renderer.
- `narrator.py`: translates engine events (`engine_alpha/events.py`) into
  channel/DM messages and the permanent `game_events` stream that powers
  `/zutomayo summary`. Reveal effects (the TAIDADA family, 03-045) broadcast
  the revealed cards to the owner's DM, the opponent's DM, and the channel, a
  fresh card-grid image per leg because a `discord.File` is consumed on send;
  revealing nothing tells the owner alone.
- `gate_presenter.py`: the phase gates. `Game.apply` runs a whole chain of
  phases at once (set cards, reveal, chronos, swaps, effects, battle, end
  turn), so `SnapshottingEventSink` - the plain list the engine already appends
  events to - captures a `BoardView` at each phase boundary as it happens. The
  `GatePresenter` posts one bundle per boundary: the abyss / power charger
  strips that changed, a phase header, the field embed, and a board image per
  perspective (channel sees the day side, each player their own). That is what
  makes the set cards visible face-down in their set slots before the reveal
  gate turns them over.
- `transport.py`: the `MatchTransport` protocol all outgoing messages route
  through - Discord DMs live, a recorder in tests, or muted during replay.
- `match_driver.py` / `match_flow.py` / `series_flow.py` / `draft_flow.py` /
  `solo_flow.py`: the driver loop and the mode orchestrators (single match,
  TCG best-of-N with side-deck switching plus the loser's day/night choice
  and one globally sequenced record for the whole series, gacha-box draft,
  solo versus a model opponent).
- `persistence.py` / `resume.py`: every game owns a permanent PostgreSQL
  record (a manifest with the engine seed and pre-shuffle decks, an
  append-only int-action decision log, the event stream). On startup, active
  games are rebuilt from their manifests and replayed from their logs with
  the transport muted, then go live and continue; `/zutomayo resume` runs the
  same machinery on demand. A fingerprint mismatch during replay marks the
  game unrecoverable and announces it.
- `agents/`: the solo opponents. `available_solo_opponents()` includes a
  model stack only when its `find_checkpoint()` locates a deployed
  checkpoint, so `/zutomayo playuniguri` turns a model down rather than
  starting a game it cannot play. Inference runs off the event loop with a
  watchdog; any agent failure submits a legal fallback action rather than
  hanging the game.

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
  renderer. The board image draws each player's cards in their zones plus a
  coin marker on the printed chronos ring showing the current time of day;
  slot coordinates and the coin size live in `board_renderer.py`.
  `card_art.py` is the shared card loader behind both the board and the card
  grids: the printed cards are round-cornered, so the jpg scans carry white
  dead space in their corners, and it masks that away in memory at render time
  from a per-pack radius. The jpg files are pristine sources - nothing rewrites
  them, and no rounded copy is ever stored.
  `image_utils.py` saves every image the bot uploads as JPEG at quality 90 with
  chroma subsampling off (4:4:4). The subsampling is the part that matters:
  Pillow defaults JPEG to 4:2:0 at *every* quality including 100, which halves
  colour resolution and smears the small text and thin coloured outlines on card
  art. Turning it off is worth roughly 10 dB PSNR. Quality is the only
  size-reduction lever - if an image exceeds the upload budget it is re-encoded
  lower, never downscaled, because the card grids are what players open to read
  effect text.
- `zutomayo/enums/`, `zutomayo/models/`, `zutomayo/utils/` - card
  attribute/type/rarity/song enums, the `Card` model, and small Discord
  helpers.

### Model stacks

`alpha_zero/` and `ppo_transformer/` are the model training stacks. Their
training code is intentionally untracked; git carries only what the bot
needs to play from a checkpoint (model definitions, configs, and the
inference modules). Of `model_common/`, that is `device.py` and
`env_config.py` plus `deployed_model.py` — everything else there is
training-only and untracked alongside the tests that exercise it.
`model_common/device.py` picks CUDA, then Apple Silicon MPS, then CPU at
runtime, and caps inference threads so the Discord event loop is never
starved.

#### Solo opponents

The weights themselves never enter git — a bare PPO state dict is ~129 MB,
over GitHub's per-file limit. They go in an untracked `model/` directory at
the repository root, one entry per stack, named after the package:

```
model/ppo_transformer     model B, the PPO transformer
model/alpha_zero          model A, the AlphaZero stack
```

The file extension is optional, and an entry may instead be a directory of
`*.pt` files, in which case the newest by name wins
(`model_common/deployed_model.py`). Deploying is therefore just a copy:

```powershell
New-Item -ItemType Directory -Force model
Copy-Item ppo_transformer\runs\latest_weights.pt model\ppo_transformer
```

`find_checkpoint()` consults `model/` first, then the older
`<stack>/deploy/model.pt` location, then the newest checkpoint under
`<stack>/runs/`, so a machine that is mid-training keeps playing with its own
run. A stack with no checkpoint anywhere is simply not offered:
`/zutomayo playuniguri model:A` answers that model A has not been trained yet.

Each stack has its own README covering configuration, running, stopping
safely, and resuming: [alpha_zero/README.md](alpha_zero/README.md) and
[ppo_transformer/README.md](ppo_transformer/README.md). See
[Training](#training) below for the short version.

The deployed AlphaZero agent plays with search rather than a single policy
forward, because training and promotion gating both select for strength with
search. `ALPHA_LIVE_MODE=policy` trades that back for latency.

Deck building is part of alpha_zero's training rather than a separate model:
`Game(mode="draft")` makes the first 40 plies an alternating 20-pick draft
from the full card pool, searched by the same MCTS with the same network, so
the win/draw/loss value head credits deck choices with the same signal it
credits plays. The league records how each drafted deck performed and
`python -m alpha_zero.scripts.export_best_decks` publishes the best ones to
`zutomayo/bot_decks.json`, the deck pool solo opponents draw from.

Games that start from fixed decks — every ppo_transformer game, and
alpha_zero's non-draft matchups — draw from the training deck pool exported by
`scripts/export_training_decks.py` (`model_common/deck_pool.py`), mixing real
player decks with generated random legal decks so unplayed cards still receive
gradient. The mix is `probability_user_deck` (default 0.75) in each stack's
config. Generated decks range over the whole card pool but take their
distinct-card count from the export's own distribution, so they share the copy
structure real decks have (players run two copies of most cards) instead of
being uniformly random. Without an export the stacks fall back to generated
decks using a measured default distribution, and say so at startup.

## Training

Both model stacks are configured the same way: dataclass defaults in
`<stack>/config.py` are the tracked baseline, and a gitignored `<stack>/.env`
layers per-machine overrides on top. Precedence is **CLI flag → process
environment → `.env` → default**.

```
ALPHA_<SECTION>_<FIELD>   ALPHA_<NAME>     alpha_zero
PPO_<SECTION>_<FIELD>     PPO_<NAME>       ppo_transformer
```

There is no checked-in `.env` template — each config module prints its own,
generated from the dataclasses so it cannot drift:

```powershell
python -m alpha_zero.config              # list every key with its default
python -m alpha_zero.config > alpha_zero\.env
python -m ppo_transformer.config > ppo_transformer\.env
```

Each stack has a smoke run, a real run, and a resume. All commands in this
section run from the repository root.

alpha_zero smoke test — two tiny iterations, confirms the loop is wired:

```powershell
python -m alpha_zero.scripts.run_train --smoke
```

alpha_zero real run, settings from `alpha_zero\.env`:

```powershell
python -m alpha_zero.scripts.run_train
```

ppo_transformer smoke test:

```powershell
python -m ppo_transformer.train.run_train --smoke
```

ppo_transformer real run, settings from `ppo_transformer\.env`:

```powershell
python -m ppo_transformer.train.run_train
```

**Stopping and resuming.** Ctrl+C, a `STOP` file in the runs directory, or
SIGTERM all wind the run down at a safe boundary: the current step finishes, a
checkpoint is written, and the resume command is printed. The `STOP` file is the
one that works for a detached run. Resuming restores the network, optimizer,
scheduler, counters and RNG state from the newest checkpoint; changing the
network shape between runs is refused with a message naming the fields rather
than failing inside `load_state_dict`.

Resume an alpha_zero run:

```powershell
python -m alpha_zero.scripts.run_train --resume
```

Resume a ppo_transformer run:

```powershell
python -m ppo_transformer.train.run_train --resume
```

Full detail, including per-stack tuning notes, is in the two stack READMEs.

Training entry points and their modules are gitignored — a fresh clone can run
the bot but not train.

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
| `scripts/export_training_decks.py` | Export every saved deck (standard plus TCG main decks) to `data/training_decks.json` as a guid plus 20 `{pack, id}` card references, deduplicated with an owner count. The guid is derived from the cards, so the same deck keeps the same guid across re-exports. This is the deck pool the model stacks train against; re-run it to refresh. `--dry-run`, `--include-defaults`, `--min-users N`. |
| `scripts/migrate_json_to_postgresql.py` | One-shot migration of the legacy JSON decks/usernames into PostgreSQL (already run). |
| `scripts/wipe_legacy_game_records.py` | One-shot cutover: clears pre-engine_alpha game records and Elo, preserving decks and names (already run). |
| `scripts/calibrate_board.py` | Draws colored zone markers onto the board image to verify renderer coordinates. Writes `calibration_output.jpg`. |
| `scripts/calibrate_chronos.py` | Draws the chronos coin marker on all 18 ring slots at once, semi-transparent with a rim and index label, to verify the coin sits centred on each printed moon/sun glyph. Writes two images: `calibration_output_chronos.jpg`, the whole board, and `calibration_output_chronos_glyphs.png`, one window per slot with the board's narrow glyph brightness band stretched to full range. Use the montage to judge alignment; the board's own contrast is too low to see an offset. The montage is the one calibration output still written as PNG, deliberately: it works by amplifying faint detail, so JPEG's ringing would land at the same amplitude as the signal it exists to reveal, and compressing it would save under 0.2 MB. |
| `scripts/measure_chronos_centers.py` | Recovers the 18 ring centres from the printed art and reports how far `CHRONOS_CENTERS` sits from each, in pixels. This is where those constants come from. Day suns are read off their disc centroid; night moons are fitted through their outer limb at a fixed radius (a free radius latches onto the gibbous terminator instead); the two new moons are a circle through their dash centroids. The two gibbous phases fit almost equally well with the disc on either side of the glyph, so the score cannot pick between them; those are resolved by ring angle, interpolated from the neighbouring slots, and both candidates are printed. Prints only. |
| `scripts/calibrate_full.py` | Every coordinate on one bare board: the card rectangles and printed slots of the first script plus the 18 chronos ring positions of the second, drawn as outlines with no card art or coins occluding them. Writes `calibration_output_full.jpg`. Reuses the overlay helpers from the two scripts above so it cannot drift from them. |
| `scripts/populate_card_images.py` | Fill the `image` field in `cards.json` with the jpg path for every card. Re-run after adding card art. |
| `scripts/convert_placeholder_cards_to_jpg.py` | One-off: gave pack-4 cards 105/106/107 a jpg (already run). They are synthetic text placeholders rather than scans, so they were the only cards that never shipped as jpg, and they are exempt from corner rounding because they have no white dead space to remove. |
| `scripts/preview_card_images.py` | Writes the images the bot uploads to `scripts/preview_*.jpg` so they can be checked by eye: a 20-card deck grid, a 25-card gacha/draft page, the same page built from the 25 worst-compressing cards in the catalog, a board, and an Abyss strip. The bytes come straight out of the renderer's `discord.File`, so each file is exactly what a player receives rather than a re-encode. Start with `preview_detail_crops.jpg` - the full grids are downscaled four or five times over by any image viewer, which hides every compression artifact, so the 1:1 crops are the only place quality is actually visible. Prints a size table alongside; expect only the worst-case page to be large enough that a 413 would trigger a re-encode. |

All calibration and preview output is gitignored. It regenerates by running the script, so it
is deliberately not committed - the same reasoning that dropped the card png derivatives in
favour of rounding corners at render time.

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
