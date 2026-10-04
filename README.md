# Zutoka Discord Bot

A Discord bot for the ZUTOMAYO CARD trading card game ([official rules](https://zutomayocard.net/start-guide/)).

## Setup

Requires Python 3.14 and PostgreSQL ([setup guide](docs/postgresql_setup.md)).

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

Create `.env` in the repository root:

| Key | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | Bot token. The local token is the development bot; production is a separate app. |
| `DATABASE_URL` | e.g. `postgresql://zutoka_bot:<password>@localhost:5432/zutoka` |

Run `python main.py`. Startup applies the schema, syncs the slash commands, and resumes games that were live at shutdown. Player data lives in PostgreSQL; card definitions live in `zutomayo/data/cards.json`.

## Commands

All commands are under `/zutomayo`.

| Command | Options | Notes |
| --- | --- | --- |
| `create` | `format` (Standard, TCG), `best_of` (3, 5; TCG only) | Game against another player. Server only. |
| `createdraft` | `boxes` (1-5; default 2, TCG 3), `visibility`, `format`, `best_of` | Both players build decks from gacha boxes they open. Server only. |
| `playuniguri` | `model` (A, B) | Solo game against a trained model, in DMs. A is `alpha_zero`, B is `ppo_transformer`. |
| `join` | `game_id` | Join a created game. Server only. |
| `end` | `game_id` | End a live game, or abandon a saved one. Server only. |
| `quit` | `save` (default False) | Leave your game; `save: True` keeps it resumable. |
| `resume` | `game_id` | Resume a saved game. Two-player games need both players to agree. |
| `deck make`, `deck view`, `deck manage` | `name` or `deck`, `format` | Build, show, or edit and delete a saved deck. |
| `gacha`, `gachabox` | `pack` (1-4) | Open a pack (5 cards) or a box (10 packs). |
| `summary` | `game_id` | Full replay of a finished game. |
| `history`, `profilestats` | `player` (optional) | Recent games; profile with Elo and stats. |
| `editname` | `name` (optional) | Set your display name. Empty reverts to your Discord name. |
| `leaderboard` | `format` | Elo ladder. |

- Game ids are `YYYYMMDD-NNNNN`: the UTC date plus a daily counter.
- Games are played in DMs. The channel only gets public narration, and solo games (channel id 0) post nothing publicly.
- `resume` from a server channel moves a two-player game's narration to that channel. From a DM, the request goes to the opponent's DM and the game keeps its channel.
- A resumed game is replayed from its decision log. If a bot update makes the replay diverge, the game is marked unrecoverable; its summary still works.
- In-game prompts time out after 300 seconds (the TCG side-deck switch and side choice: 750) and apply a fallback action. Three timeouts in a row forfeit the game.
- Deck building times out after 750 seconds and deals a random deck. Draft picking has no timeout.

## Formats

- **Standard:** 20-card deck, at most 2 copies of a card.
- **TCG:** best of 3 or 5, with a 20-card main deck and an 8-card side deck. Between matches both players may swap cards between main and side decks. The previous match's loser then picks day or night; match 1 is a coin flip, and a drawn match is replayed with the same chooser. Elo moves once per series.
- **Draft:** sealed Standard or TCG. Each player opens 1-5 gacha boxes (50 cards each) and picks 20 cards (TCG: 28, of which 8 form the side deck), at most 2 copies of each.
- **Solo:** a Standard game against a model, in DMs. See [Solo opponents](#solo-opponents).

Playing a CHAOS bank-or-lose card (04-006, 04-027, 04-028, 04-088, 04-105) without enough abyss cards loses the game at once. On turn 1 of a two-player non-TCG game, the winner gains no Elo and the loser takes a heavy Elo penalty (never below 0). Win/loss and deck stats count normally. The penalty is silent, so its numbers are deliberately not documented.

## Architecture

| Path | Role |
| --- | --- |
| `engine_alpha/` | Rules engine: a deterministic, clonable state machine that imports nothing from `zutomayo`. See [engine_alpha/README.md](engine_alpha/README.md). |
| `zutomayo/match/` | Match runtime: decision broker, Discord presentation, narration, phase gates, persistence and resume, and the mode flows (single, TCG series, draft, solo). |
| `zutomayo/cogs/game_cog.py` | Every `/zutomayo` command. |
| `zutomayo/engine/` | Session bookkeeping, the game-record store, and the event taxonomy. |
| `zutomayo/data/` | asyncpg storage (schema, decks, profiles and Elo, display names, game ids), gacha, and card loading. |
| `zutomayo/ui/` | Embeds, the board renderer, card grids, and Discord views. |
| `alpha_zero/`, `ppo_transformer/`, `model_common/` | Model stacks. Only the code needed to play from a checkpoint is tracked, plus `ppo_transformer/best_deck_ppo.py`. |

- Every player choice after deck building is a `MatchDecisionRequest` answered through `MatchDecisionBroker` (`zutomayo/match/broker.py`). The broker rejects illegal answers and appends each answer to the game's decision log.
- Games are deterministic: the seed drives every shuffle, and each decision is a logged int (card keys for a TCG side-deck switch). On restart or `/zutomayo resume`, a game is rebuilt from its manifest and the log is replayed with output muted, checking each request's fingerprint. Draft boxes are opened before the game record exists, so only the resulting decks are saved.
- All outgoing match messages go through a `MatchTransport`: Discord when live, a recorder in tests, muted during replay.

## Solo opponents

`/zutomayo playuniguri` offers a model only once its checkpoint is found. Checkpoints exceed GitHub's 100 MB file limit, so deploy one by copying it into the untracked `model/` directory:

```powershell
New-Item -ItemType Directory -Force model
Copy-Item ppo_transformer\runs\checkpoints\iteration_00800.pt model\ppo_transformer
```

| Model | Deployed entry | Fallbacks, in order |
| --- | --- | --- |
| A | `model/alpha_zero` | `alpha_zero/deploy/model.pt`, newest `alpha_zero/runs/checkpoints/step_*.pt` |
| B | `model/ppo_transformer` | `ppo_transformer/deploy/model.pt`, `ppo_transformer/runs/latest_weights.pt`, newest `ppo_transformer/runs/checkpoints/iteration_*.pt` |

- An entry may be a file (extension optional) or a directory of `*.pt` files, where the last by name wins.
- Prefer a file from `runs/checkpoints/`: it carries its config. Bare weights (`latest_weights.pt`) load with the `NetConfig` defaults in the stack's `config.py`.
- The model plays a random deck from `zutomayo/bot_decks.json` (gitignored, written by `python -m alpha_zero.scripts.export_best_decks`), or from `zutomayo/default_decks.json` when that file is absent.
- Model A searches with `ALPHA_LIVE_SIMULATIONS` (default 64) simulations per decision; `ALPHA_LIVE_MODE=policy` plays the raw policy instead.
- Inference runs off the event loop behind a 45-second watchdog. Any failure submits a legal fallback action.
- Models see the full game state, including the opponent's hand and deck order. This is deliberate.

## Training

Training code is gitignored, so only a training machine can train. Each stack's README (also gitignored) has the details. Run everything from the repository root:

```powershell
python -m alpha_zero.scripts.run_train --smoke     # two tiny iterations
python -m alpha_zero.scripts.run_train             # real run
python -m alpha_zero.scripts.run_train --resume
python -m ppo_transformer.train.run_train --smoke  # same flags for ppo_transformer
```

- Settings resolve in this order: CLI flag, process environment, the stack's `.env`, then the dataclass default in `config.py`. Keys are `ALPHA_<SECTION>_<FIELD>` and `PPO_<SECTION>_<FIELD>`, plus run-level `ALPHA_<NAME>` and `PPO_<NAME>`.
- `alpha_zero/config.py` is the baseline, and `alpha_zero/.env` overrides it. `ppo_transformer/.env` sets every key and is the source of truth.
- `python -m alpha_zero.config` (or `ppo_transformer.config`) prints every key with its default. To save that output as a `.env` in PowerShell, pipe it to `Out-File -Encoding utf8 alpha_zero\.env`; a plain `>` writes UTF-16, which the loader rejects.
- Ctrl+C, SIGTERM, or a `STOP` file in the runs directory stops a run safely: it writes a checkpoint and prints the resume command.

`scripts/export_training_decks.py` writes both stacks' deck pools from the database in one pass. Re-run it when the player meta shifts.

| | `data/training_decks_ppo.json` | `data/training_decks_alpha_zero.json` |
| --- | --- | --- |
| Decks holding a CHAOS card | dropped | kept |
| `probability_user_deck` | 1.0 | 0.75 (the rest are generated) |

Without a pool, alpha_zero falls back to generated decks and ppo_transformer refuses to start.

## Scripts

Run from the repository root. Database scripts read `DATABASE_URL` from `.env`.

| Script | Purpose |
| --- | --- |
| `apply_schema.py` | Apply the schema by hand (idempotent; startup does this too). |
| `export_database.py`, `import_database.py` | Portable JSON dump and load of every table. Import upserts; supports `--replace` and `--dry-run`. |
| `dump_database.py`, `restore_database.py` | Binary backup and restore with `pg_dump` / `pg_restore`. |
| `postgresql_tools.py` | Finds the PostgreSQL client binaries (`PGBIN`, PATH, default install paths). |
| `database_transfer.py` | Table specs and serializers shared by export and import. |
| `reset_elo.py` | Reset one Elo ladder for everyone: `--format standard` or `--format tcg`. |
| `export_training_decks.py` | Export saved decks as the training deck pools (training machines only). |
| `populate_card_images.py` | Fill the `image` field of every card in `cards.json`. |
| `calibrate_board.py`, `calibrate_chronos.py`, `calibrate_full.py` | Draw the renderer's coordinates on the board to check alignment. |
| `measure_chronos_centers.py` | Measure the chronos ring centres from the board art and report drift from `CHRONOS_CENTERS`. |
| `preview_card_images.py` | Write the images the bot uploads to `scripts/preview_*.jpg` for a visual check. |
| `migrate_json_to_postgresql.py`, `wipe_legacy_game_records.py`, `convert_placeholder_cards_to_jpg.py` | One-time migrations, already run. |

Calibration and preview output is gitignored.

## Tests

```
python tests/run_all.py                               # pytest, transcript compare, coverage gates
python -m pytest engine_alpha/tests tests -q          # unit tests
python tests/run_match_regression.py compare          # 24 seeded games vs golden transcripts
python -m engine_alpha.scripts.fuzz                   # engine invariant fuzzer
python -m engine_alpha.scripts.bench_engine           # engine performance gate
python -m engine_alpha.scripts.transcript --seed 11   # readable transcript of one game
```

- Tests swap storage for in-memory fakes (`tests/fakes.py`), so no database is needed.
- The PostgreSQL integration tests in `tests/data/` also run when `ZUTOKA_TEST_DATABASE_URL` points at a scratch database. Set it in the shell, because tests do not read `.env`. Without these tests, the data-layer coverage gate fails.
- Coverage gates are set in `tests/run_all.py`. Raise them; never lower them.
- Regenerate the golden transcripts (`run_match_regression.py write`) only for an intended behavior change.
- `tests/test_source_encoding.py` rejects byte order marks and Windows-ANSI mojibake in text files. `.editorconfig` keeps editors on UTF-8.
