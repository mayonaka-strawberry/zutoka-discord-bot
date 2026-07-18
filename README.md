# Zutoka Discord Bot

A Discord bot implementing the ZUTOMAYO CARD trading card game. Rules follow the official game
(https://zutomayocard.net/start-guide/).

## Setup

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
`python scripts/apply_schema.py`). Back up or move the database with the
scripts described in [docs/postgresql_setup.md](docs/postgresql_setup.md).

## Commands

All slash commands live under `/zutomayo` (12 top-level entries):

- `create` - one command for every game mode: `format: Standard | TCG`,
  `deck: Saved | Draft`, `opponent` (another player, or - once a trained
  model checkpoint is deployed - a solo game in DMs), plus `best_of`
  (TCG), `boxes` and `visibility` (draft).
- `join <game_id>`, `end <game_id>`, `quit [save: True|False]` (save
  replaces the old saveandquit), `resume <game_id>` (two-player resumes need
  both players to confirm).
- `deck make | view | manage` - each takes `format: Standard | TCG`; view and
  manage search your saved decks with autocomplete.
- `gacha pack: [amount: Pack | Box]` - one pack of 5 cards or a box of 10 packs.
- `summary <game_id>` (full replay of a finished game), `history [player]`,
  `profilestats [player]`, `editname [name]`, `leaderboard [format]`.

Game ids are `YYYYMMDD-XXXXX` (UTC date plus a daily counter). Saving a game
keeps no partial results; resuming replays the game deterministically from
its decision log, so saved games are best-effort across bot updates (a
diverged game is marked unrecoverable, but its summary keeps working).

## Architecture

The game rules run on **engine_alpha** (see
[engine_alpha/README.md](engine_alpha/README.md)): a self-contained,
deterministic state machine - `Game(seed, mode, decks)` with
`decision_context() / legal_actions() / apply(action)` - covering all 250
card effects as declarative IR. The bot wraps it:

- `zutomayo/match/` - the match runtime:
  - `decisions.py` / `broker.py`: every interactive choice is a
    `MatchDecisionRequest` answered through the `MatchDecisionBroker` as one
    engine action int. Timeouts resolve to a deterministic fallback action;
    three consecutive timeouts forfeit.
  - `presentation.py` / `discord_adapter.py`: maps engine decisions onto the
    Discord views; the mulligan and set-cards prompts are compound (one view
    answers the engine's iterative requests, both players prompted
    concurrently).
  - `state_view.py`: read-only `BoardView` / `PlayerView` / `CardView`
    projections consumed by embeds and the PIL board renderer.
  - `narrator.py`: translates engine events (`engine_alpha/events.py`) into
    channel/DM messages and the permanent `game_events` stream that powers
    `/zutomayo summary`.
  - `match_driver.py` / `match_flow.py` / `series_flow.py` / `draft_flow.py` /
    `solo_flow.py`: the driver loop and the mode orchestrators (single match,
    TCG best-of-N with side-deck switching, gacha-box draft, solo versus a
    model opponent).
  - `persistence.py` / `resume.py`: every game owns a permanent PostgreSQL record
    (manifest with the engine seed and decks, an append-only int-action
    decision log, the event stream). On startup, active games replay from
    their logs (transport muted) and continue live; `/zutomayo resume` runs
    the same machinery on demand.
  - `agents/`: the solo opponents. `available_solo_opponents()` discovers
    deployable checkpoints from the model stacks; solo choices appear in
    `/zutomayo create` only when one exists.
- `zutomayo/engine/` - session bookkeeping (`game_session.py`), the
  PostgreSQL game-record backend (`game_persistence.py`), the event
  taxonomy (`game_events.py`).
- `zutomayo/data/` - card catalog loading (cached), deck persistence and
  validators, player profiles with Elo, display names, gacha.
- `zutomayo/ui/` - embeds, the PIL board renderer (run off-thread), the
  interactive Discord views, the game summary renderer.
- `alpha_zero/` and `ppo_transformer/` - the model training stacks (their
  training code is intentionally untracked; git carries only the model
  definitions, configs, and inference modules the bot needs to play from a
  checkpoint). `model_common/device.py` picks CUDA, then Apple Silicon MPS,
  then CPU at runtime.

Determinism contract: the engine seed drives the coin flip and every shuffle,
and every decision is one logged int applied in sequence - that is what makes
restart replay, `/zutomayo resume`, and the transcript regression suite
possible. Event recording is observation-only and suppressed during replay.

## Tests and verification

```
python tests/run_all.py                          # everything: pytest + coverage gates + transcript compare
python -m pytest engine_alpha/tests tests -q     # unit suite
python tests/run_match_regression.py compare     # 24 seeded full-stack games vs golden transcripts
python -m engine_alpha.scripts.fuzz              # engine invariant fuzzer
```

The suite runs without a database (in-memory backends are installed by
`tests/conftest.py`). PostgreSQL integration tests run additionally when
`ZUTOKA_TEST_DATABASE_URL` is set. Transcript baselines live in
`tests/baselines/match/`; regenerate with `write` instead of `compare` only
when a behavior change is intended.

## Formats

- Standard: 20-card deck, up to 2 copies per card.
- TCG: best-of-3 or 5 with a 20-card main deck plus an 8-card side deck;
  between matches both players may swap cards between main and side decks.
  Per-match stats are tracked separately; the TCG Elo ladder moves once per
  completed series.
- Draft: sealed variants of both formats. Each player opens gacha boxes
  (1-5) and builds a deck only from the opened cards, picking through a
  paginated menu in DM. Downstream play, Elo, and leaderboards are identical
  to the non-draft formats.
- Solo: a standard game against a trained model opponent in DMs, available
  once a checkpoint from `alpha_zero/` or `ppo_transformer/` is deployed.
