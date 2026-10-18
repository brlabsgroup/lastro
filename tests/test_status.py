"""`data status`: counts from the database, files from disk, last run of each command."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import fetch, parquet, status
from lastro.db import store

URL = f"{fetch.BASE_URL}/data/spot/monthly/klines/{{s}}/{{i}}/{{s}}-{{i}}-{{m}}.zip"


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()
    rows = [
        ("BTCUSDT", "1d", "2021-01", "verified"), ("BTCUSDT", "1d", "2021-02", "verified"),
        ("BTCUSDT", "1h", "2021-01", "downloaded"), ("BTCUSDT", "1h", "2021-02", "pending"),
        ("ETHUSDT", "1d", "2021-01", "failed"), ("ETHUSDT", "1h", "2021-01", "pending"),
    ]
    with store.transaction(connection):
        for s, i, m, st in rows:
            connection.execute(
                "INSERT INTO archive_files (symbol, interval, month, url, size_bytes, status) VALUES (?, ?, ?, ?, 10, ?)",
                (s, i, m, URL.format(s=s, i=i, m=m), st),
            )
        connection.execute(
            "INSERT INTO runs (command, parameters, started_at, finished_at, outcome, detail) VALUES "
            "('data list', '{}', 't0', 't1', 'ok', '2 pairs, 6 files listed in 1 s'), "
            "('data fetch', '{}', 't2', 't3', 'partial', '1 downloaded'), "
            "('data fetch', '{}', 't4', 't5', 'ok', '2 downloaded, 1 already on disk')"
        )
        connection.execute(
            "INSERT INTO quality_issues (symbol, interval, period_start, period_end, check_name, detail, action, detected_at) "
            "VALUES ('BTCUSDT', '1d', 'a', 'b', 'gap', 'x', 'recorded', 't')"
        )
        connection.execute(
            "INSERT INTO universe (jurisdiction, decision_date, symbol, rank, median_volume_30, history_days, eligible, exclusion_reason) VALUES "
            "('', '2021-02-01', 'BTCUSDT', 1, 1.0, 90, 1, NULL), ('', '2021-02-02', 'BTCUSDT', 1, 1.0, 91, 1, NULL), "
            "('BR', '2021-02-01', 'BTCUSDT', 1, 1.0, 90, 1, NULL)"
        )
        connection.execute(
            "INSERT INTO dataset_manifest (version, frozen_at, cutoff_month, dataset_hash, file_count, rows_by_interval) "
            "VALUES ('2021-01', 't', '2021-01', 'abc123', 6, '{}')"
        )
    for s, i, m, st in rows:
        if st in ("downloaded", "verified"):
            url = URL.format(s=s, i=i, m=m)
            path = fetch.local_path(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"0123456789")
            if st == "verified":
                fetch.checksum_path(url).write_text("abc  x.zip\n")
    part = parquet.partition_path("1d", "BTCUSDT", 2021)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"not really parquet but 30 bytes long!!")
    book = config.parquet_dir() / "orderbook" / "symbol=BTCUSDT" / "date=2026-10-17" / "120000.parquet"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"x")
    yield connection
    connection.close()


def test_collect_counts_database_and_disk(conn):
    s = status.collect(conn)
    assert s.pairs == 2
    assert s.by_interval == {
        "1d": {"verified": 2, "failed": 1},
        "1h": {"downloaded": 1, "pending": 2},
    }
    assert s.files == 6 and s.total("pending") == 2 and s.total("verified") == 2
    assert (s.zips.files, s.zips.bytes) == (3, 30)
    assert s.checksums.files == 2
    assert (s.partitions.files, s.partitions.bytes) == (1, 38)  # the order book file is not a candle partition
    assert s.orderbook.files == 1
    assert s.quality_issues == 1
    assert s.last_runs["data fetch"] == ("t5", "ok", "2 downloaded, 1 already on disk")  # the latest run, not the first
    assert s.last_runs["data list"][1] == "ok"
    assert "data convert" not in s.last_runs


def test_render_and_summary(conn):
    lines = status.render(status.collect(conn))
    assert lines[0] == "archive files (2 pairs, from `lastro data list`):"
    assert lines[1].split() == ["interval", "pending", "downloaded", "verified", "failed", "total"]
    assert lines[2].split() == ["1d", "0", "0", "2", "1", "3"]
    assert lines[3].split() == ["1h", "2", "1", "0", "0", "3"]
    assert lines[4].split() == ["total", "2", "1", "2", "1", "6"]
    assert lines[5] == "on disk: 3 zips (0.0 MB), 2 .CHECKSUM files, 1 candle partitions (0.0 MB), 1 order book files"
    assert lines[6] == "quality issues recorded: 1"
    assert lines[7] == "universe '-': 2 rows, latest decision date 2021-02-02"
    assert lines[8] == "universe 'BR': 1 rows, latest decision date 2021-02-01"
    assert lines[9] == "frozen dataset 2021-01: hash abc123"
    assert lines[10] == "last run of each command:"
    assert lines[11].split(maxsplit=4) == ["data", "list", "t1", "ok", "2 pairs, 6 files listed in 1 s"]
    assert status.summary(status.collect(conn)) == "6 archive files, 2 pending, 1 downloaded, 2 verified, 1 failed; 1 partitions on disk"


def test_cli_status(conn):
    result = CliRunner().invoke(app, ["data", "status"])
    assert result.exit_code == 0, result.output
    assert "data dir:" in result.output
    assert "ok      6 archive files, 2 pending, 1 downloaded, 2 verified, 1 failed; 1 partitions on disk" in result.output


def test_cli_status_on_empty_database(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    store.init()[0].close()
    result = CliRunner().invoke(app, ["data", "status"])
    assert result.exit_code == 0, result.output
    assert "ok      0 archive files; 0 partitions on disk" in result.output
