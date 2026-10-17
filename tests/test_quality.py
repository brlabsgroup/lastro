"""Quality checks: a clean pair finds nothing, a pair with one planted defect per check finds each exactly once."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import fetch, parquet, quality
from lastro.db import store

DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
JAN1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAILY_VOLUME = 10.0


def day(n: int) -> datetime:
    """Day n of 2024 (0 = January 1)."""
    return JAN1 + n * DAY


def row(symbol: str, at: datetime, close: float, volume: float, source: str, **overrides) -> dict:
    r = {
        "symbol": symbol, "open_time": at,
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": volume, "quote_volume": volume * close, "trades": 10 if volume else 0,
        "taker_buy_base": volume / 2, "taker_buy_quote": volume * close / 2,
        "source_file": source,
    }
    r.update(overrides)
    return r


def write_rows(interval: str, symbol: str, rows: list[dict]) -> None:
    """Write partitions straight from rows (no sorting, no de-duplication: defects stay planted)."""
    by_year: dict[int, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["open_time"].year, []).append(r)
    for year, part in by_year.items():
        path = parquet.partition_path(interval, symbol, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(part, schema=parquet.SCHEMA), path)


def mark_verified(conn, symbol: str, interval: str, months: list[str]) -> None:
    with store.transaction(conn):
        for month in months:
            name = f"{symbol}-{interval}-{month}.zip"
            url = f"{fetch.BASE_URL}/data/spot/monthly/klines/{symbol}/{interval}/{name}"
            conn.execute(
                "INSERT INTO archive_files (symbol, interval, month, url, size_bytes, status) VALUES (?, ?, ?, ?, 1, 'verified')",
                (symbol, interval, month, url),
            )


def daily_rows(symbol: str, days: int, close_of, volume_of) -> list[dict]:
    rows = []
    prev_close = None
    for n in range(days):
        at = day(n)
        volume = volume_of(n)
        close = close_of(n) if volume else prev_close  # zero-volume candles repeat the last price
        rows.append(row(symbol, at, close, volume, f"{symbol}-1d-{at:%Y-%m}.zip"))
        prev_close = close
    return rows


def hourly_rows(symbol: str, first_day: int, days: int, close_of, volume_of) -> list[dict]:
    rows = []
    for n in range(first_day, first_day + days):
        for h in range(24):
            at = day(n) + h * HOUR
            rows.append(row(symbol, at, close_of(n), volume_of(n) / 24, f"{symbol}-1h-{at:%Y-%m}.zip"))
    return rows


# Defects planted in BADUSDT, one per check. Day numbers: 0 = Jan 1, 31 = Feb 1, 60 = Mar 1 (2024 is a leap year).
JAN10, JAN20, JAN25, JAN26 = 9, 19, 24, 25
FEB10, FEB14, FEB20 = 40, 44, 50
MAR1, MAR5, MAR10, MAR13, MAR20 = 60, 64, 69, 72, 79
OFF_GRID = timedelta(minutes=28, seconds=14)  # the offset Binance published after its February 2018 outage

EXPECTED_BAD = {
    "coverage": 1,        # BADUSDT 1d 2024-04 verified, no rows
    "cross_interval": 1,  # Mar 20: hourly volumes sum to 11, daily says 10
    "duplicate": 1,       # Jan 20 twice
    "gap": 2,             # Jan 10 missing (recorded); Feb 10-14 missing (period flagged)
    "jump": 2,            # Jan 5 (+152%, no 1h data); Mar 1 (+152%, confirmed by 1h)
    "ohlc": 1,            # Feb 20 high below close
    "timestamp": 1,       # 1h Mar 5 11:00-13:00 published at hh:28:14, one finding for the run of 3
    "zero_volume": 3,     # 1d Jan 25-26 (recorded); 1d Mar 10-13 (suspended); 1h Mar 10-13, 96 candles (suspended)
}


def bad_close(n: int) -> float:
    base = 100.0 + n
    if n < 4:
        return base * 0.4  # Jan 1-4 low, so Jan 5 jumps +152%
    if n >= MAR1:
        return base * 2.5  # Mar 1 jumps +152% and stays there
    return base


def bad_volume(n: int) -> float:
    return 0.0 if n in (JAN25, JAN26) or MAR10 <= n <= MAR13 else DAILY_VOLUME


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()

    # GOODUSDT: January to March daily, January hourly, everything consistent.
    good = daily_rows("GOODUSDT", 91, lambda n: 100.0 + n, lambda n: DAILY_VOLUME)
    write_rows("1d", "GOODUSDT", good)
    write_rows("1h", "GOODUSDT", hourly_rows("GOODUSDT", 0, 31, lambda n: 100.0 + n, lambda n: DAILY_VOLUME))
    mark_verified(connection, "GOODUSDT", "1d", ["2024-01", "2024-02", "2024-03"])
    mark_verified(connection, "GOODUSDT", "1h", ["2024-01"])

    # BADUSDT: the same shape with one defect per check.
    bad = daily_rows("BADUSDT", 91, bad_close, bad_volume)
    bad = [r for r in bad if r["open_time"] != day(JAN10)]                       # gap of 1
    bad = [r for r in bad if not day(FEB10) <= r["open_time"] <= day(FEB14)]     # gap of 5
    bad.append(dict(next(r for r in bad if r["open_time"] == day(JAN20))))       # duplicate
    feb20 = next(r for r in bad if r["open_time"] == day(FEB20))
    feb20["high"] = feb20["close"] * 0.9                                         # ohlc
    write_rows("1d", "BADUSDT", bad)
    bad_hourly = hourly_rows(
        "BADUSDT", MAR1, 31, bad_close,
        lambda n: 0.0 if MAR10 <= n <= MAR13 else (11.0 if n == MAR20 else DAILY_VOLUME),  # cross_interval
    )
    for r in bad_hourly:
        if day(MAR5) + 11 * HOUR <= r["open_time"] <= day(MAR5) + 13 * HOUR:
            r["open_time"] += OFF_GRID                                              # timestamp (off grid)
    write_rows("1h", "BADUSDT", bad_hourly)
    mark_verified(connection, "BADUSDT", "1d", ["2024-01", "2024-02", "2024-03", "2024-04"])  # coverage: April
    mark_verified(connection, "BADUSDT", "1h", ["2024-03"])

    yield connection
    connection.close()


@pytest.fixture
def hard(conn):
    """HARDUSDT: a candle in the future, one before 2017 (unit misread), an off-grid one, and an unverified zip."""
    rows = daily_rows("HARDUSDT", 31, lambda n: 100.0, lambda n: DAILY_VOLUME)
    rows[4]["open_time"] = day(4) + 12 * HOUR
    rows[9]["open_time"] = datetime(2099, 1, 1, tzinfo=timezone.utc)
    rows[14]["open_time"] = datetime(1970, 1, 20, tzinfo=timezone.utc)  # 2024 in ms read as us
    write_rows("1d", "HARDUSDT", rows)
    return conn


def test_clean_pair_has_no_findings(conn):
    result = quality.run_checks(conn, symbols=["GOODUSDT"])
    assert result.issues == [] and result.hard_failures == []
    assert result.rows_checked == 91 + 31 * 24
    assert result.intervals_checked == ["1d", "1h"] and result.cross_checked
    assert result.missing == []


def test_each_planted_defect_is_found_once(conn):
    result = quality.run_checks(conn, symbols=["BADUSDT"])
    assert result.hard_failures == []
    assert quality.QualityResult.counts(result.issues) == EXPECTED_BAD
    by_check = {}
    for issue in result.issues:
        by_check.setdefault(issue.check, []).append(issue)

    gaps = by_check["gap"]
    assert (gaps[0].period_start, gaps[0].period_end, gaps[0].detail, gaps[0].action) == (
        "2024-01-09T00:00:00+00:00", "2024-01-11T00:00:00+00:00", "1 candles missing", "recorded")
    assert gaps[1].detail == "5 candles missing" and gaps[1].action == "period flagged for the universe"

    grid = by_check["timestamp"][0]
    assert (grid.interval, grid.period_start, grid.period_end) == ("1h", "2024-03-05T11:28:14+00:00", "2024-03-05T13:28:14+00:00")
    assert grid.detail == "3 candles off the 1h grid (offset 28:14)"
    assert grid.action == "period flagged for the universe"

    zero = by_check["zero_volume"]
    assert [(z.interval, z.detail, z.action) for z in zero] == [
        ("1d", "2 consecutive zero-volume candles", "recorded"),
        ("1d", "4 consecutive zero-volume candles", "pair flagged suspended for the period"),
        ("1h", "96 consecutive zero-volume candles", "pair flagged suspended for the period"),
    ]
    assert zero[1].period_start == "2024-03-10T00:00:00+00:00" and zero[1].period_end == "2024-03-13T00:00:00+00:00"

    jumps = by_check["jump"]
    assert jumps[0].period_start == "2024-01-05T00:00:00+00:00"
    assert "no 1h data" in jumps[0].detail and jumps[0].action == "recorded; unconfirmed"
    assert jumps[1].period_start == "2024-03-01T00:00:00+00:00"
    assert "confirmed by 1h" in jumps[1].detail and jumps[1].action == "recorded; confirmed"
    assert jumps[1].detail.startswith("return +151.6%")

    assert by_check["duplicate"][0].period_start == "2024-01-20T00:00:00+00:00"
    assert by_check["ohlc"][0].period_start == "2024-02-20T00:00:00+00:00"
    assert by_check["ohlc"][0].action == "row excluded from the universe"
    assert (by_check["coverage"][0].period_start, by_check["coverage"][0].interval) == ("2024-04", "1d")
    cross = by_check["cross_interval"][0]
    assert cross.interval == "1h/1d" and cross.period_start == "2024-03-20T00:00:00+00:00"
    assert "+10.0%" in cross.detail


def test_interval_filter_skips_the_cross_checks(conn):
    result = quality.run_checks(conn, intervals=("1d",), symbols=["BADUSDT"])
    counts = quality.QualityResult.counts(result.issues)
    assert "cross_interval" not in counts
    assert counts["zero_volume"] == 2  # the 1h run is not checked
    assert all("1h not checked" in j.detail for j in result.issues if j.check == "jump")


def test_hard_failures_are_reported_and_stop_the_command(hard):
    result = quality.run_checks(hard, symbols=["HARDUSDT"])
    assert quality.QualityResult.counts(result.hard_failures) == {"source": 1, "timestamp": 2}
    details = sorted(i.detail for i in result.hard_failures)
    assert details == [
        "HARDUSDT-1d-2024-01.zip is not a verified archive file",
        "open_time before the archive begins",
        "open_time in the future",
    ]
    assert all(i.action == "hard failure" for i in result.hard_failures)
    # The off-grid candle is the archive's doing, not ours: a finding, with the period flagged.
    grid = [i for i in result.issues if i.check == "timestamp"]
    assert [(i.period_start, i.detail, i.action) for i in grid] == [
        ("2024-01-05T12:00:00+00:00", "1 candles off the 1d grid (offset 12:00:00)", "period flagged for the universe"),
    ]
    # The out-of-range rows are left out of the gap arithmetic; only the days they displaced are missing.
    assert [i.detail for i in result.issues if i.check == "gap"] == ["1 candles missing", "1 candles missing"]

    cli = CliRunner().invoke(app, ["data", "quality", "-s", "HARDUSDT"])
    assert cli.exit_code == 1, cli.output
    assert "FAIL  HARDUSDT 1d 2099-01-01 00:00:00: open_time in the future -> hard failure" in cli.output
    assert "FAIL  HARDUSDT 1d 1970-01-20 00:00:00: open_time before the archive begins -> hard failure" in cli.output
    assert "FAILED  quality checks" in cli.output
    assert "why: 3 hard failures (source 1, timestamp 2)" in cli.output
    assert "fix: source: rows came from zips that are not verified" in cli.output
    assert "timestamp: candles before 2017 or in the future mean the converter misread the unit" in cli.output
    stored = hard.execute("SELECT COUNT(*) FROM quality_issues WHERE action = 'hard failure'").fetchone()[0]
    assert stored == 3
    assert hard.execute("SELECT outcome FROM runs ORDER BY id DESC LIMIT 1").fetchone()["outcome"] == "failed"


def test_record_replaces_findings_for_the_checked_scope_only(conn):
    result = quality.run_checks(conn, symbols=["BADUSDT"])
    assert quality.record(conn, result, ["BADUSDT"]) == sum(EXPECTED_BAD.values())
    assert quality.record(conn, result, ["BADUSDT"]) == sum(EXPECTED_BAD.values())
    assert conn.execute("SELECT COUNT(*) FROM quality_issues").fetchone()[0] == sum(EXPECTED_BAD.values())

    # Checking another pair, or only one interval, leaves the rest untouched.
    quality.record(conn, quality.run_checks(conn, symbols=["GOODUSDT"]), ["GOODUSDT"])
    assert conn.execute("SELECT COUNT(*) FROM quality_issues").fetchone()[0] == sum(EXPECTED_BAD.values())
    only_1h = quality.run_checks(conn, intervals=("1h",), symbols=["BADUSDT"])
    quality.record(conn, only_1h, ["BADUSDT"])
    kept = conn.execute("SELECT interval, COUNT(*) AS n FROM quality_issues GROUP BY interval ORDER BY interval").fetchall()
    assert [(r["interval"], r["n"]) for r in kept] == [("1d", 9), ("1h", 2), ("1h/1d", 1)]

    row_ = conn.execute("SELECT * FROM quality_issues WHERE check_name = 'coverage'").fetchone()
    assert (row_["symbol"], row_["interval"], row_["period_start"], row_["action"]) == ("BADUSDT", "1d", "2024-04", "month treated as absent")
    assert row_["detected_at"]


def test_cli_quality_prints_summary_and_examples(conn):
    cli = CliRunner().invoke(app, ["data", "quality", "-s", "BADUSDT", "-s", "GOODUSDT"])
    assert cli.exit_code == 0, cli.output
    assert "  1d: 177 rows in 2 partitions" in cli.output
    assert "  1h: 1488 rows in 2 partitions" in cli.output
    assert "1h vs 1d: cross-checked" in cli.output
    assert "  gap             2" in cli.output
    assert "    BADUSDT 1d 2024-01-09 00:00:00 .. 2024-01-11 00:00:00: 1 candles missing -> recorded" in cli.output
    assert "    BADUSDT 1d 2024-04: verified month has no rows in Parquet -> month treated as absent" in cli.output
    assert "    BADUSDT 1h 2024-03-05 11:28:14 .. 2024-03-05 13:28:14: 3 candles off the 1h grid (offset 28:14) -> period flagged for the universe" in cli.output
    assert "ok      1665 rows checked, 12 findings recorded in quality_issues (coverage 1, cross_interval 1, duplicate 1, gap 2, jump 2, ohlc 1, timestamp 1, zero_volume 3)" in cli.output
    assert conn.execute("SELECT outcome FROM runs").fetchone()["outcome"] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM quality_issues").fetchone()[0] == 12


def test_cli_quality_clean_pair(conn):
    cli = CliRunner().invoke(app, ["data", "quality", "-s", "GOODUSDT"])
    assert cli.exit_code == 0, cli.output
    assert "ok      835 rows checked, 0 findings recorded in quality_issues" in cli.output


def test_cli_quality_fails_without_parquet(conn):
    cli = CliRunner().invoke(app, ["data", "quality", "-s", "NOPEUSDT"])
    assert cli.exit_code == 1, cli.output
    assert "why: no Parquet partitions found for NOPEUSDT" in cli.output
    assert "fix: run `lastro data fetch`, `lastro data verify` and `lastro data convert` first" in cli.output

    cli = CliRunner().invoke(app, ["data", "quality", "-s", "GOODUSDT", "-s", "NOPEUSDT"])
    assert cli.exit_code == 0, cli.output
    assert "no Parquet for NOPEUSDT" in cli.output


def test_cli_quality_rejects_unknown_interval(conn):
    cli = CliRunner().invoke(app, ["data", "quality", "-i", "15m"])
    assert cli.exit_code == 1, cli.output
    assert "why: unknown interval 15m; use one of 1d, 4h, 1h, 30m, 5m" in cli.output
