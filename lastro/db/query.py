"""DuckDB connections for reading the Parquet market data.

Every connection is pinned to UTC. DuckDB otherwise renders timestamps in the
machine's local time zone when handing them to Python, which turns a 00:00 UTC
daily candle into "21:00 the day before" on a Buenos Aires laptop. Market data
in Lastro is UTC everywhere, with no exceptions.
"""

from __future__ import annotations

import duckdb

from lastro import config


def connect() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("SET TimeZone = 'UTC'")
    return conn


def klines_glob(interval: str | None = None, symbol: str | None = None) -> str:
    """Glob covering the candle partitions, optionally narrowed to one interval and/or pair."""
    root = config.parquet_dir() / "klines"
    i = f"interval={interval}" if interval else "interval=*"
    s = f"symbol={symbol}" if symbol else "symbol=*"
    return str(root / i / s / "year=*" / "part.parquet")
