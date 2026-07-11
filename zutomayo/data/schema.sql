-- Zutoka Discord bot PostgreSQL schema.
-- Applied idempotently at bot startup (zutomayo.data.database.apply_schema)
-- and manually via scripts/apply_schema.py.
--
-- Card definitions stay in zutomayo/data/cards.json and are never stored here.

CREATE TABLE IF NOT EXISTS schema_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_profiles (
    user_id        BIGINT PRIMARY KEY,
    last_updated   TIMESTAMPTZ,
    elo            INTEGER NOT NULL DEFAULT 1000,
    elo_peak       INTEGER NOT NULL DEFAULT 1000,
    elo_games      INTEGER NOT NULL DEFAULT 0,
    tcg_elo        INTEGER NOT NULL DEFAULT 1000,
    tcg_elo_peak   INTEGER NOT NULL DEFAULT 1000,
    tcg_elo_games  INTEGER NOT NULL DEFAULT 0,
    stats          JSONB NOT NULL DEFAULT '{}',
    deck_stats     JSONB NOT NULL DEFAULT '{}',
    opponent_stats JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS player_profiles_elo_index ON player_profiles (elo DESC);
CREATE INDEX IF NOT EXISTS player_profiles_tcg_elo_index ON player_profiles (tcg_elo DESC);

CREATE TABLE IF NOT EXISTS display_names (
    user_id    BIGINT PRIMARY KEY,
    name       TEXT NOT NULL,
    custom     BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decks (
    user_id    BIGINT NOT NULL,
    name       TEXT NOT NULL,
    cards      JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS decks_tcg (
    user_id    BIGINT NOT NULL,
    name       TEXT NOT NULL,
    main_deck  JSONB NOT NULL,
    side_deck  JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS daily_game_counters (
    day          DATE PRIMARY KEY,
    next_counter INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    game_id         TEXT PRIMARY KEY,
    schema_version  INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
                    ('active', 'saved', 'completed', 'quit', 'abandoned', 'divergence_failed')),
    mode            TEXT NOT NULL,
    channel_id      BIGINT NOT NULL,
    is_solo         BOOLEAN NOT NULL,
    solo_difficulty TEXT NOT NULL DEFAULT 'normal',
    is_tcg          BOOLEAN NOT NULL,
    best_of         INTEGER NOT NULL DEFAULT 0,
    random_seed     NUMERIC(20, 0) NOT NULL,
    manifest        JSONB NOT NULL,
    winner_index    SMALLINT,
    result_summary  JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    saved_at        TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS games_status_index ON games (status);
CREATE INDEX IF NOT EXISTS games_created_at_index ON games (created_at DESC);

CREATE TABLE IF NOT EXISTS game_players (
    game_id      TEXT NOT NULL REFERENCES games (game_id) ON DELETE CASCADE,
    player_index SMALLINT NOT NULL,
    discord_id   BIGINT NOT NULL,
    deck_name    TEXT,
    PRIMARY KEY (game_id, player_index)
);
CREATE INDEX IF NOT EXISTS game_players_discord_id_index ON game_players (discord_id);

CREATE TABLE IF NOT EXISTS game_decisions (
    game_id         TEXT NOT NULL REFERENCES games (game_id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    fingerprint     JSONB NOT NULL,
    payload_type    TEXT NOT NULL,
    payload         JSONB,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS game_events (
    game_id      TEXT NOT NULL REFERENCES games (game_id) ON DELETE CASCADE,
    event_index  INTEGER NOT NULL,
    match_number SMALLINT,
    turn         SMALLINT,
    phase        TEXT,
    event_type   TEXT NOT NULL,
    payload      JSONB NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, event_index)
);
CREATE INDEX IF NOT EXISTS game_events_type_index ON game_events (game_id, event_type);

CREATE TABLE IF NOT EXISTS elo_history (
    game_id     TEXT NOT NULL REFERENCES games (game_id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL,
    ladder      TEXT NOT NULL CHECK (ladder IN ('standard', 'tcg')),
    elo_before  INTEGER NOT NULL,
    elo_after   INTEGER NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, user_id, ladder)
);
CREATE INDEX IF NOT EXISTS elo_history_user_index ON elo_history (user_id, recorded_at DESC);

INSERT INTO schema_metadata (key, value) VALUES ('schema_version', '1')
ON CONFLICT (key) DO NOTHING;
