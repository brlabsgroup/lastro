-- Lastro operational database, Phase 1 tables.
-- Applied once by lastro.db.store; never edited after being applied. Changes go in a new numbered file.

-- Every zip known to exist in the public archive, and what happened to it locally.
CREATE TABLE archive_files (
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    month             TEXT    NOT NULL,              -- YYYY-MM
    url               TEXT    NOT NULL,
    size_bytes        INTEGER,
    expected_checksum TEXT,
    verified_checksum TEXT,
    downloaded_at     TEXT,                          -- ISO 8601 UTC
    status            TEXT    NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'downloaded', 'verified', 'failed')),
    PRIMARY KEY (symbol, interval, month)
);

-- Every pair ever seen in the archive enumeration, alive or dead.
CREATE TABLE symbols (
    symbol        TEXT PRIMARY KEY,
    base_asset    TEXT,
    quote_asset   TEXT,
    first_month   TEXT,                              -- YYYY-MM
    last_month    TEXT,                              -- YYYY-MM
    last_candle   TEXT,                              -- ISO 8601 UTC, last candle with volume
    alive_today   INTEGER NOT NULL DEFAULT 0,        -- 0/1, from the latest exchangeInfo snapshot
    alias_of      TEXT,                              -- symbol this one continues, when a rename is confirmed
    updated_at    TEXT    NOT NULL
);

-- One row per failed quality check.
CREATE TABLE quality_issues (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    interval    TEXT NOT NULL,
    period_start TEXT NOT NULL,                      -- ISO 8601 UTC
    period_end  TEXT NOT NULL,                       -- ISO 8601 UTC
    check_name  TEXT NOT NULL,
    detail      TEXT,
    action      TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

-- Point-in-time eligibility: every candidate on every decision date, not only the top 50.
CREATE TABLE universe (
    decision_date    TEXT    NOT NULL,               -- YYYY-MM-DD
    symbol           TEXT    NOT NULL,
    rank             INTEGER,
    median_volume_30 REAL,
    eligible         INTEGER NOT NULL,               -- 0/1
    exclusion_reason TEXT,
    PRIMARY KEY (decision_date, symbol)
);

-- Dated restriction lists per jurisdiction (e.g. the 22 tokens restricted in Brazil from 2026-10-27).
CREATE TABLE restrictions (
    jurisdiction TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    valid_from   TEXT NOT NULL,                      -- YYYY-MM-DD
    valid_to     TEXT,                               -- YYYY-MM-DD, NULL = still in force
    reason       TEXT,
    PRIMARY KEY (jurisdiction, symbol, valid_from)
);

-- One row per frozen dataset version.
CREATE TABLE dataset_manifest (
    version      TEXT PRIMARY KEY,
    frozen_at    TEXT NOT NULL,                      -- ISO 8601 UTC
    cutoff_month TEXT NOT NULL,                      -- YYYY-MM, last complete month included
    dataset_hash TEXT NOT NULL,
    file_count   INTEGER NOT NULL,
    rows_by_interval TEXT                            -- JSON object, interval -> row count
);

-- Every command execution.
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    command     TEXT NOT NULL,
    parameters  TEXT,                                -- JSON
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT,                                -- 'ok', 'failed', or a short message
    detail      TEXT
);
