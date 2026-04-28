-- TennisIQ — Full Database Schema
-- Run: psql $DATABASE_URL -f schema.sql
-- Idempotent: all CREATE statements use IF NOT EXISTS

-- ──────────────────────────────────────────────────────────
-- Players
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS players (
    player_id       SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    name_variants   TEXT[],
    tour            TEXT,
    hand            TEXT,
    height_cm       INT,
    birthdate       DATE,
    country         TEXT,
    -- Elo columns (populated by elo_engine.py)
    elo_overall     FLOAT   DEFAULT 1500,
    elo_hard        FLOAT   DEFAULT 1500,
    elo_clay        FLOAT   DEFAULT 1500,
    elo_grass       FLOAT   DEFAULT 1500,
    elo_display     FLOAT   DEFAULT 1500,
    fifa_rating     INTEGER DEFAULT NULL,
    card_tier       VARCHAR(10) DEFAULT NULL,
    elo_peak        FLOAT   DEFAULT 1500,
    elo_peak_date   DATE    DEFAULT NULL,
    elo_match_count INTEGER DEFAULT 0,
    elo_last_updated TIMESTAMPTZ DEFAULT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
DROP INDEX IF EXISTS idx_players_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_players_name ON players(name);
CREATE INDEX IF NOT EXISTS idx_players_elo_display ON players(elo_display DESC);

-- ──────────────────────────────────────────────────────────
-- Tournaments
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tournaments (
    tournament_id   SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    surface         TEXT,
    category        TEXT,
    latitude        FLOAT,
    longitude       FLOAT,
    ball_brand      TEXT,
    court_pace_idx  FLOAT,
    microclimate    JSONB,
    level           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
DROP INDEX IF EXISTS idx_tournaments_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tournaments_name ON tournaments(name);

-- ──────────────────────────────────────────────────────────
-- Matches
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS matches (
    match_id        SERIAL PRIMARY KEY,
    tournament_id   INT REFERENCES tournaments(tournament_id),
    match_date      DATE,
    round           TEXT,
    surface         TEXT,
    winner_id       INT REFERENCES players(player_id),
    loser_id        INT REFERENCES players(player_id),
    score           TEXT,
    duration_min    INT,
    has_charting    BOOLEAN DEFAULT FALSE,
    source          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_matches_date    ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_winner  ON matches(winner_id);
CREATE INDEX IF NOT EXISTS idx_matches_loser   ON matches(loser_id);
CREATE INDEX IF NOT EXISTS idx_matches_surface ON matches(surface);

-- ──────────────────────────────────────────────────────────
-- Points (from Match Charting Project)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS points (
    point_id        SERIAL PRIMARY KEY,
    match_id        INT REFERENCES matches(match_id),
    set_num         INT,
    game_num        INT,
    point_num       INT,
    server_id       INT REFERENCES players(player_id),
    returner_id     INT REFERENCES players(player_id),
    serve_num       INT,
    serve_dir       TEXT,
    serve_depth     TEXT,
    rally_sequence  TEXT,
    rally_length    INT,
    outcome         TEXT,
    winner_id       INT REFERENCES players(player_id),
    score_before    TEXT,
    is_break_point  BOOLEAN,
    is_set_point    BOOLEAN,
    is_match_point  BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_points_match   ON points(match_id);
CREATE INDEX IF NOT EXISTS idx_points_server  ON points(server_id);
CREATE INDEX IF NOT EXISTS idx_points_returner ON points(returner_id);

-- ──────────────────────────────────────────────────────────
-- Shots (parsed from rally_sequence)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shots (
    shot_id         SERIAL PRIMARY KEY,
    point_id        INT REFERENCES points(point_id),
    shot_num        INT,
    player_id       INT REFERENCES players(player_id),
    shot_type       TEXT,
    direction       TEXT,
    depth           TEXT,
    outcome         TEXT,
    is_approach     BOOLEAN,
    came_to_net     BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_shots_point  ON shots(point_id);
CREATE INDEX IF NOT EXISTS idx_shots_player ON shots(player_id);

-- ──────────────────────────────────────────────────────────
-- File Read Status (incremental pipeline bookkeeping)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS file_read_status (
    file_name            TEXT PRIMARY KEY,
    last_read_timestamp  TIMESTAMPTZ NOT NULL,
    latest_row_timestamp TIMESTAMPTZ
);

-- ──────────────────────────────────────────────────────────
-- Elo History
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS elo_history (
    id               SERIAL PRIMARY KEY,
    player_id        TEXT NOT NULL,
    match_id         INTEGER,
    match_date       DATE,
    surface          VARCHAR(10),
    elo_before       FLOAT,
    elo_after        FLOAT,
    opponent_elo     FLOAT,
    tournament_level VARCHAR(20),
    k_factor         INTEGER,
    UNIQUE (player_id, match_id)
);
CREATE INDEX IF NOT EXISTS idx_elo_history_player ON elo_history(player_id);
CREATE INDEX IF NOT EXISTS idx_elo_history_date   ON elo_history(match_date);

-- ──────────────────────────────────────────────────────────
-- Player Style Profiles (pre-computed by feature_engine.py)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS player_profiles (
    profile_id          SERIAL PRIMARY KEY,
    player_id           INT REFERENCES players(player_id),
    surface             TEXT,
    computed_at         TIMESTAMPTZ,
    match_count         INT,
    data_confidence     TEXT,               -- 'low' | 'moderate' | 'high'
    -- Serve
    serve_wide_pct      FLOAT,
    serve_body_pct      FLOAT,
    serve_t_pct         FLOAT,
    ace_rate            FLOAT,
    first_serve_pct     FLOAT,
    first_serve_won     FLOAT,
    second_serve_won    FLOAT,
    -- Rally
    fh_cross_pct        FLOAT,
    fh_line_pct         FLOAT,
    bh_cross_pct        FLOAT,
    bh_line_pct         FLOAT,
    approach_rate       FLOAT,
    net_point_won_pct   FLOAT,
    avg_rally_length    FLOAT,
    -- Shot mix
    slice_rate          FLOAT,
    topspin_rate        FLOAT,
    uf_error_rate       FLOAT,
    winner_rate         FLOAT,
    -- Pressure
    bp_save_pct         FLOAT,
    bp_convert_pct      FLOAT,
    clutch_delta        FLOAT,
    -- Style cluster
    archetype           TEXT,
    archetype_confidence FLOAT,
    -- Card attributes (SRV/RET/PAT/SPD/HRD/CLY)
    attr_srv            INTEGER DEFAULT 50,
    attr_ret            INTEGER DEFAULT 50,
    attr_pat            INTEGER DEFAULT 50,
    attr_spd            INTEGER DEFAULT 50,
    attr_hrd            INTEGER DEFAULT 50,
    attr_cly            INTEGER DEFAULT 50,
    -- Feature vector (JSON)
    feature_vector      JSONB,
    UNIQUE (player_id, surface)
);
CREATE INDEX IF NOT EXISTS idx_profiles_player ON player_profiles(player_id);

-- ──────────────────────────────────────────────────────────
-- Weather readings
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_readings (
    id              SERIAL PRIMARY KEY,
    match_date      DATE,
    tournament_id   INT REFERENCES tournaments(tournament_id),
    temp_c          FLOAT,
    humidity_pct    FLOAT,
    wind_kph        FLOAT,
    precipitation_mm FLOAT,
    roof_closed     BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_weather_date ON weather_readings(match_date);
