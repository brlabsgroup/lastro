"""Build the point-in-time universe from the daily candles.

For every decision date D and every pair that traded on D-1, one row says
whether the pair was eligible, and if not, why. Only data up to the close of
D-1 is used. Exclusions, in the order they are applied:

1. Quote asset: the strategy trades USDT pairs; BTCBUSD or USDTBRL are
   duplicates or fiat legs, and their volumes are not even in the same unit.
2. Asset type: leveraged tokens, stablecoins, fiat currencies, wrapped or
   pegged assets (``assets.py``).
3. Jurisdiction: a restriction in force on D for the requested jurisdiction.
4. History: fewer than ``min_history`` traded days since the pair's history
   last restarted. The clock restarts after a gap longer than ``long_gap_days``,
   which is what turns a symbol reused for a different asset (LUNA in 2022)
   into a new listing.
5. Quality: a recorded finding on 1d data that covers D-1 and says the row or
   period must not be used (bad OHLC, off-grid run, suspension). Gaps are not
   applied here: the missing candles already remove those days, and applying a
   gap to the day before it starts would use information from the future.
6. Liquidity: a 30-day median of quote volume below ``min_volume``.

Eligible pairs are ranked by that median, 1 = most liquid. Every candidate is
stored, not only the top of the ranking, so the strategy can apply its own
membership rules and anyone can audit why a pair was out on a given day.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pyarrow as pa

from lastro.data import quality
from lastro.data.archive import QUOTE
from lastro.db import query, store
from lastro.universe import assets

MIN_VOLUME = 500_000.0  # quote units per day, median over 30 traded days
MIN_HISTORY = 60  # traded days since the last long gap
LONG_GAP_DAYS = 7  # a gap longer than this restarts a pair's history
EXCLUDING_ACTIONS = ("row excluded from the universe", "period flagged for the universe", "pair flagged suspended for the period")
BATCH = 50_000


class UniverseError(RuntimeError):
    pass


@dataclass
class BuildResult:
    jurisdiction: str = ""
    dates: int = 0
    rows: int = 0
    first_date: str | None = None
    last_date: str | None = None
    last_eligible: int = 0
    last_top: list[tuple[str, float]] = field(default_factory=list)  # (symbol, median volume) on the last date
    last_exclusions: dict[str, int] = field(default_factory=dict)


def _known_bases(conn: sqlite3.Connection) -> set[str]:
    bases = {r["base_asset"] for r in conn.execute("SELECT DISTINCT base_asset FROM symbols WHERE base_asset IS NOT NULL").fetchall()}
    bases |= {r["base_asset"] for r in conn.execute("SELECT DISTINCT base_asset FROM exchange_symbols").fetchall()}
    return bases


def _type_table(conn: sqlite3.Connection) -> pa.Table:
    known = _known_bases(conn)
    rows = conn.execute("SELECT symbol, base_asset FROM symbols WHERE base_asset IS NOT NULL").fetchall()
    out = [(r["symbol"], reason) for r in rows if (reason := assets.exclusion_reason(r["base_asset"], known))]
    return pa.table({"symbol": [s for s, _ in out], "reason": [r for _, r in out]}, schema=pa.schema([("symbol", pa.string()), ("reason", pa.string())]))


def _restriction_table(conn: sqlite3.Connection, jurisdiction: str) -> pa.Table:
    rows = conn.execute(
        "SELECT symbol, valid_from, valid_to FROM restrictions WHERE jurisdiction = ?", (jurisdiction,)
    ).fetchall() if jurisdiction else []
    return pa.table(
        {
            "symbol": [r["symbol"] for r in rows],
            "valid_from": pa.array([date.fromisoformat(r["valid_from"]) for r in rows], pa.date32()),
            "valid_to": pa.array([date.fromisoformat(r["valid_to"]) if r["valid_to"] else None for r in rows], pa.date32()),
        }
    )


def _flag_table(conn: sqlite3.Connection) -> pa.Table:
    placeholders = ",".join("?" * len(EXCLUDING_ACTIONS))
    rows = conn.execute(
        f"SELECT symbol, period_start, period_end, check_name FROM quality_issues "
        f"WHERE interval = '1d' AND check_name != 'gap' AND action IN ({placeholders})",
        EXCLUDING_ACTIONS,
    ).fetchall()
    return pa.table(
        {
            "symbol": [r["symbol"] for r in rows],
            "start": pa.array([date.fromisoformat(r["period_start"][:10]) for r in rows], pa.date32()),
            "end": pa.array([date.fromisoformat(r["period_end"][:10]) for r in rows], pa.date32()),
            "check_name": [r["check_name"] for r in rows],
        }
    )


def build(
    conn: sqlite3.Connection,
    jurisdiction: str = "",
    min_volume: float = MIN_VOLUME,
    min_history: int = MIN_HISTORY,
    long_gap_days: int = LONG_GAP_DAYS,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> BuildResult:
    """Compute every candidate row for the date range and replace the stored universe for that scope."""
    files = quality.partition_files("1d", symbols)
    if not files:
        raise UniverseError("no daily Parquet partitions; run `lastro data convert` first")
    db = query.connect()
    quality._view(db, "candles", files)
    db.register("types", _type_table(conn))
    db.register("restr", _restriction_table(conn, jurisdiction))
    db.register("flags", _flag_table(conn))
    where_dates = []
    if start:
        where_dates.append(f"decision_date >= DATE '{start.isoformat()}'")
    if end:
        where_dates.append(f"decision_date <= DATE '{end.isoformat()}'")
    date_filter = ("WHERE " + " AND ".join(where_dates)) if where_dates else ""
    sql = f"""
        WITH traded AS (
            SELECT symbol, open_time::DATE AS day, quote_volume FROM candles WHERE volume > 0
        ),
        seq AS (
            SELECT symbol, day, quote_volume,
                   CASE WHEN LAG(day) OVER (PARTITION BY symbol ORDER BY day) IS NULL
                          OR day - LAG(day) OVER (PARTITION BY symbol ORDER BY day) > {int(long_gap_days)}
                        THEN 1 ELSE 0 END AS restart
            FROM traded
        ),
        seg AS (
            SELECT *, SUM(restart) OVER (PARTITION BY symbol ORDER BY day ROWS UNBOUNDED PRECEDING) AS segment FROM seq
        ),
        feat AS (
            SELECT symbol, day, day + 1 AS decision_date,
                   ROW_NUMBER() OVER (PARTITION BY symbol, segment ORDER BY day) AS history_days,
                   median(quote_volume) OVER (PARTITION BY symbol, segment ORDER BY day ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS median_volume_30
            FROM seg
        ),
        judged AS (
            SELECT f.decision_date, f.symbol, f.median_volume_30, f.history_days,
                   COALESCE(
                       CASE WHEN f.symbol NOT LIKE '%{QUOTE}' THEN 'quote asset is not {QUOTE}' END,
                       t.reason,
                       CASE WHEN r.symbol IS NOT NULL THEN 'restricted in {jurisdiction or "-"}' END,
                       CASE WHEN f.history_days < {int(min_history)} THEN 'history shorter than {int(min_history)} traded days' END,
                       CASE WHEN q.symbol IS NOT NULL THEN 'quality: ' || q.check_name END,
                       CASE WHEN f.median_volume_30 < {float(min_volume)} THEN 'median volume below minimum' END
                   ) AS exclusion_reason
            FROM feat f
            LEFT JOIN types t ON t.symbol = f.symbol
            LEFT JOIN restr r ON r.symbol = f.symbol AND r.valid_from <= f.decision_date AND (r.valid_to IS NULL OR f.decision_date < r.valid_to)
            LEFT JOIN (SELECT DISTINCT ON (symbol, day) * FROM (
                           SELECT fl.symbol, fe.day, fl.check_name FROM flags fl JOIN feat fe ON fe.symbol = fl.symbol AND fe.day BETWEEN fl.start AND fl."end"
                       ) ORDER BY symbol, day, check_name) q ON q.symbol = f.symbol AND q.day = f.day
        ),
        ranked AS (
            SELECT *, CASE WHEN exclusion_reason IS NULL
                           THEN RANK() OVER (PARTITION BY decision_date ORDER BY CASE WHEN exclusion_reason IS NULL THEN 0 ELSE 1 END, median_volume_30 DESC, symbol)
                      END AS rank
            FROM judged
        )
        SELECT decision_date, symbol, rank, median_volume_30, history_days,
               CASE WHEN exclusion_reason IS NULL THEN 1 ELSE 0 END AS eligible, exclusion_reason
        FROM ranked {date_filter}
        ORDER BY decision_date, eligible DESC, rank, symbol
    """
    cursor = db.execute(sql)
    result = BuildResult(jurisdiction=jurisdiction)
    with store.transaction(conn):
        delete = "DELETE FROM universe WHERE jurisdiction = ?"
        params: list[object] = [jurisdiction]
        if start:
            delete += " AND decision_date >= ?"
            params.append(start.isoformat())
        if end:
            delete += " AND decision_date <= ?"
            params.append(end.isoformat())
        if symbols:
            delete += f" AND symbol IN ({','.join('?' * len(symbols))})"
            params.extend(symbols)
        conn.execute(delete, params)
        dates: set[str] = set()
        last_rows: list[tuple] = []
        while True:
            batch = cursor.fetchmany(BATCH)
            if not batch:
                break
            conn.executemany(
                "INSERT INTO universe (jurisdiction, decision_date, symbol, rank, median_volume_30, history_days, eligible, exclusion_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(jurisdiction, d.isoformat(), s, rk, mv, hd, el, ex) for d, s, rk, mv, hd, el, ex in batch],
            )
            result.rows += len(batch)
            for row in batch:
                dates.add(row[0].isoformat())
            last_date = batch[-1][0]
            last_rows = [r for r in last_rows if r[0] == last_date] + [r for r in batch if r[0] == last_date]
            if progress and result.rows % (BATCH * 4) == 0:
                progress(f"{result.rows} rows, up to {last_date.isoformat()}")
    result.dates = len(dates)
    if dates:
        result.first_date, result.last_date = min(dates), max(dates)
        eligible = [r for r in last_rows if r[5] == 1]
        result.last_eligible = len(eligible)
        result.last_top = [(r[1], float(r[3])) for r in sorted(eligible, key=lambda r: r[2])[:10]]
        for r in last_rows:
            if r[6]:
                result.last_exclusions[r[6]] = result.last_exclusions.get(r[6], 0) + 1
    return result


def show(conn: sqlite3.Connection, decision_date: date, jurisdiction: str = "", top: int = 50) -> tuple[list[sqlite3.Row], dict[str, int]]:
    """The ranking stored for one decision date, plus the exclusion counts."""
    rows = conn.execute(
        "SELECT symbol, rank, median_volume_30, history_days FROM universe "
        "WHERE jurisdiction = ? AND decision_date = ? AND eligible = 1 ORDER BY rank LIMIT ?",
        (jurisdiction, decision_date.isoformat(), top),
    ).fetchall()
    excluded = conn.execute(
        "SELECT exclusion_reason, COUNT(*) AS n FROM universe WHERE jurisdiction = ? AND decision_date = ? AND eligible = 0 "
        "GROUP BY exclusion_reason ORDER BY n DESC",
        (jurisdiction, decision_date.isoformat()),
    ).fetchall()
    return rows, {r["exclusion_reason"]: r["n"] for r in excluded}


def latest_date(conn: sqlite3.Connection, jurisdiction: str = "") -> date | None:
    row = conn.execute("SELECT MAX(decision_date) AS d FROM universe WHERE jurisdiction = ?", (jurisdiction,)).fetchone()
    return date.fromisoformat(row["d"]) if row["d"] else None
