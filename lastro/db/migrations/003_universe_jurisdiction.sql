-- Phase 1, piece 8: the universe carries the jurisdiction it was built for, and the history length used.
-- The table was empty until this piece landed; it is recreated rather than altered because SQLite cannot
-- change a primary key in place.
DROP TABLE universe;

CREATE TABLE universe (
    jurisdiction     TEXT    NOT NULL DEFAULT '',   -- '' = no jurisdiction restrictions applied
    decision_date    TEXT    NOT NULL,              -- YYYY-MM-DD; built from data up to the previous day's close
    symbol           TEXT    NOT NULL,
    rank             INTEGER,                       -- 1 = most liquid among the eligible; NULL when not eligible
    median_volume_30 REAL,                          -- median quote volume over the trailing 30 traded days
    history_days     INTEGER,                       -- traded days since the last long gap, inclusive of D-1
    eligible         INTEGER NOT NULL,              -- 0/1
    exclusion_reason TEXT,
    PRIMARY KEY (jurisdiction, decision_date, symbol)
);
