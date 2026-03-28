-- PlayHQ Fixtures & Scores Database Schema
-- Hierarchy: Association > Season > Grade > Fixtures/Teams

CREATE TABLE IF NOT EXISTS associations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,        -- "Penrith District Netball Association"
    sport           TEXT,                        -- "netball-australia"
    address         TEXT,                        -- from footer
    url             TEXT                         -- PlayHQ org page
);

CREATE TABLE IF NOT EXISTS seasons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    association_id  INTEGER NOT NULL REFERENCES associations(id),
    name            TEXT NOT NULL,               -- "Representative & Development Carnival 12yrs to Opens"
    year            INTEGER,                     -- 2026
    url             TEXT UNIQUE NOT NULL,        -- the season page URL
    last_scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS grades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id       INTEGER NOT NULL REFERENCES seasons(id),
    name            TEXT NOT NULL,               -- "U15 Girls Development"
    gender          TEXT,                        -- "Girls", "Women"
    age_group       TEXT,                        -- "U12", "U13", "U15", "U17"
    url             TEXT UNIQUE,                 -- the grade fixtures page URL
    UNIQUE(season_id, name)
);

CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    grade_id        INTEGER NOT NULL REFERENCES grades(id),
    UNIQUE(name, grade_id)
);

CREATE TABLE IF NOT EXISTS fixtures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    grade_id        INTEGER NOT NULL REFERENCES grades(id),
    round_name      TEXT NOT NULL,               -- "Round 1", "Semi Final"
    date            TEXT,
    time            TEXT,
    venue           TEXT,
    court           TEXT,
    home_team_id    INTEGER NOT NULL REFERENCES teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(id),
    home_score      INTEGER,                     -- NULL if not yet played
    away_score      INTEGER,
    status          TEXT NOT NULL DEFAULT 'scheduled',
    UNIQUE(grade_id, round_name, home_team_id, away_team_id)
);
