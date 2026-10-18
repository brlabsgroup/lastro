-- Phase 1, piece 7: exchange metadata snapshots and currency rates.

-- One row per symbol per daily exchangeInfo capture. History starts the day the first snapshot is taken;
-- the archive enumeration (symbols.first_month/last_month) covers what came before.
CREATE TABLE exchange_symbols (
    captured_on   TEXT    NOT NULL,                  -- YYYY-MM-DD (UTC)
    symbol        TEXT    NOT NULL,
    status        TEXT    NOT NULL,                  -- TRADING, BREAK, HALT, ...
    base_asset    TEXT    NOT NULL,
    quote_asset   TEXT    NOT NULL,
    spot_allowed  INTEGER NOT NULL,                  -- 0/1, isSpotTradingAllowed
    tick_size     REAL,                              -- PRICE_FILTER.tickSize
    step_size     REAL,                              -- LOT_SIZE.stepSize
    min_qty       REAL,                              -- LOT_SIZE.minQty
    min_notional  REAL,                              -- NOTIONAL.minNotional (or MIN_NOTIONAL on older payloads)
    captured_at   TEXT    NOT NULL,                  -- ISO 8601 UTC
    PRIMARY KEY (captured_on, symbol)
);

-- Daily currency rates from each source, one row per date and source.
CREATE TABLE fx_rates (
    date        TEXT NOT NULL,                       -- YYYY-MM-DD
    source      TEXT NOT NULL,                       -- ptax_sell (SGS 1), ptax_buy (SGS 10813), usdtbrl_close
    rate        REAL NOT NULL,                       -- BRL per USD (or per USDT)
    captured_at TEXT NOT NULL,
    PRIMARY KEY (date, source)
);
