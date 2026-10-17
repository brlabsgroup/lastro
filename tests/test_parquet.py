"""Conversion tests: synthetic monthly zips in both timestamp units, with and without header."""

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import fetch, parquet
from lastro.db import query, store

DAY_US = 86_400_000_000


def ts(year: int, month: int, day: int) -> int:
    """Microseconds since the epoch at 00:00 UTC."""
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1_000_000)


def candle_line(open_us: int, unit: str, price: float, volume: float = 1.0) -> str:
    stamp = open_us if unit == "us" else open_us // 1000
    close = stamp + (DAY_US - 1 if unit == "us" else DAY_US // 1000 - 1)
    return f"{stamp},{price},{price * 1.01},{price * 0.99},{price},{volume},{close},{volume * price},10,{volume / 2},{volume * price / 2},0"


def month_zip(symbol: str, year: int, month: int, days: int, unit: str, header: bool, zero_from: int | None = None) -> bytes:
    lines = []
    if header:
        lines.append("open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore")
    for d in range(1, days + 1):
        volume = 0.0 if zero_from is not None and d >= zero_from else 1.0
        lines.append(candle_line(ts(year, month, d), unit, 100.0 + d, volume))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{symbol}-1d-{year}-{month:02d}.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


MONTHS = {
    # (symbol, year, month): (days, unit, header, zero_from)
    ("BTCUSDT", 2024, 11): (30, "ms", False, None),
    ("BTCUSDT", 2024, 12): (31, "ms", True, None),
    ("BTCUSDT", 2025, 1): (31, "us", False, None),
    ("BTCBUSD", 2023, 11): (30, "ms", False, None),
    ("BTCBUSD", 2023, 12): (31, "ms", False, 11),  # trades until Dec 10, zero volume after
}


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()
    with store.transaction(connection):
        for (symbol, year, month), (days, unit, header, zero_from) in MONTHS.items():
            label = f"{year}-{month:02d}"
            url = f"{fetch.BASE_URL}/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{label}.zip"
            body = month_zip(symbol, year, month, days, unit, header, zero_from)
            path = fetch.local_path(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            connection.execute(
                "INSERT INTO archive_files (symbol, interval, month, url, size_bytes, status) VALUES (?, '1d', ?, ?, ?, 'verified')",
                (symbol, label, url, len(body)),
            )
        for symbol in ("BTCUSDT", "BTCBUSD"):
            connection.execute(
                "INSERT INTO symbols (symbol, base_asset, quote_asset, first_month, last_month, updated_at) VALUES (?, 'BTC', ?, '2023-11', '2025-01', 'x')",
                (symbol, symbol[3:]),
            )
    yield connection
    connection.close()


def test_normalize_timestamp_handles_both_units():
    assert parquet.normalize_timestamp("1735689600000") == 1735689600000000  # ms -> us
    assert parquet.normalize_timestamp("1735689600000000") == 1735689600000000  # already us
    with pytest.raises(parquet.ConvertError):
        parquet.normalize_timestamp("-5")


def test_read_zip_skips_header_and_keeps_all_rows(tmp_path: Path):
    path = tmp_path / "x.zip"
    path.write_bytes(month_zip("X", 2024, 12, 31, "ms", header=True))
    rows = parquet.read_zip(path, "X", "x.zip")
    assert len(rows) == 31
    assert rows[0]["open_time"] == ts(2024, 12, 1)
    assert rows[0]["source_file"] == "x.zip" and rows[0]["symbol"] == "X"
    assert rows[0]["trades"] == 10


def test_read_zip_rejects_bad_zip_and_bad_width(tmp_path: Path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(parquet.ConvertError, match="not a valid zip"):
        parquet.read_zip(bad, "X", "bad.zip")
    narrow = tmp_path / "narrow.zip"
    with zipfile.ZipFile(narrow, "w") as zf:
        zf.writestr("X.csv", "1,2,3\n")
    with pytest.raises(parquet.ConvertError, match="expected 12"):
        parquet.read_zip(narrow, "X", "narrow.zip")


def test_convert_writes_partitions_by_year(conn):
    result = parquet.convert_verified(conn)
    assert result.failed == []
    assert result.files == 5
    assert result.rows == 30 + 31 + 31 + 30 + 31
    assert result.partitions == 3  # BTCUSDT 2024, BTCUSDT 2025, BTCBUSD 2023
    assert parquet.partition_path("1d", "BTCUSDT", 2024).exists()
    assert parquet.partition_path("1d", "BTCUSDT", 2025).exists()
    assert parquet.partition_path("1d", "BTCBUSD", 2023).exists()


def test_converted_data_is_utc_sorted_and_unit_normalized(conn):
    parquet.convert_verified(conn)
    db = query.connect()
    rows = db.execute(
        f"SELECT open_time, open FROM read_parquet('{query.klines_glob('1d', 'BTCUSDT')}') ORDER BY open_time"
    ).fetchall()
    assert len(rows) == 92
    # Continuous daily sequence across the ms->us boundary (Dec 2024 -> Jan 2025).
    deltas = {(b[0] - a[0]).total_seconds() for a, b in zip(rows, rows[1:])}
    assert deltas == {86400.0}
    assert rows[0][0].tzinfo is not None and rows[0][0].utcoffset().total_seconds() == 0
    assert rows[0][0] == datetime(2024, 11, 1, tzinfo=timezone.utc)
    assert rows[-1][0] == datetime(2025, 1, 31, tzinfo=timezone.utc)


def test_last_candle_is_the_last_day_with_volume(conn):
    parquet.convert_verified(conn)
    rows = {r["symbol"]: r["last_candle"] for r in conn.execute("SELECT symbol, last_candle FROM symbols").fetchall()}
    assert rows["BTCUSDT"] == "2025-01-31T00:00:00+00:00"
    assert rows["BTCBUSD"] == "2023-12-10T00:00:00+00:00"  # zero volume from the 11th on


def test_convert_is_idempotent(conn):
    first = parquet.convert_verified(conn)
    second = parquet.convert_verified(conn)
    assert (first.rows, first.partitions) == (second.rows, second.partitions)
    assert not parquet.partition_path("1d", "BTCUSDT", 2024).with_suffix(".parquet.tmp").exists()


def test_convert_skips_partition_with_a_broken_month(conn):
    url = conn.execute("SELECT url FROM archive_files WHERE symbol = 'BTCUSDT' AND month = '2024-12'").fetchone()["url"]
    fetch.local_path(url).write_bytes(b"corrupted")
    result = parquet.convert_verified(conn)
    assert len(result.failed) == 1 and result.failed[0][0] == "BTCUSDT/1d/2024-12"
    assert not parquet.partition_path("1d", "BTCUSDT", 2024).exists()  # whole year withheld
    assert parquet.partition_path("1d", "BTCUSDT", 2025).exists()  # other years unaffected
    assert parquet.partition_path("1d", "BTCBUSD", 2023).exists()


def test_cli_convert(conn):
    result = CliRunner().invoke(app, ["data", "convert", "-s", "BTCBUSD"])
    assert result.exit_code == 0, result.output
    assert "ok      2 files, 61 rows into 1 partitions" in result.output
    assert conn.execute("SELECT outcome FROM runs").fetchone()["outcome"] == "ok"


def test_duckdb_connection_is_pinned_to_utc(monkeypatch):
    """Regardless of the machine's zone, timestamps come back as UTC."""
    monkeypatch.setenv("TZ", "America/Buenos_Aires")
    db = query.connect()
    value = db.execute("SELECT TIMESTAMPTZ '2024-11-01 00:00:00+00'").fetchone()[0]
    assert value == datetime(2024, 11, 1, tzinfo=timezone.utc)
    assert value.utcoffset().total_seconds() == 0
