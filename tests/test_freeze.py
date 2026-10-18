"""Freeze: the manifest names the dataset by content, and a copy of raw/ reproduces the same hash elsewhere."""

import hashlib
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import checksum, fetch, freeze, parquet
from lastro.db import store
from test_parquet import MONTHS, month_zip


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    """The test_parquet months, verified with real digests and converted."""
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
            digest = hashlib.sha256(body).hexdigest()
            connection.execute(
                "INSERT INTO archive_files (symbol, interval, month, url, size_bytes, expected_checksum, verified_checksum, status) "
                "VALUES (?, '1d', ?, ?, ?, ?, ?, 'verified')",
                (symbol, label, url, len(body), digest, digest),
            )
        for symbol in ("BTCUSDT", "BTCBUSD"):
            connection.execute(
                "INSERT INTO symbols (symbol, base_asset, quote_asset, first_month, last_month, updated_at) VALUES (?, 'BTC', ?, '2023-11', '2025-01', 'x')",
                (symbol, symbol[3:]),
            )
    parquet.convert_verified(connection)
    yield connection
    connection.close()


def test_cutoff_parsing():
    assert freeze.cutoff_end_micros("2024-12") == 1735689600 * 1_000_000  # 2025-01-01 00:00 UTC
    assert freeze.cutoff_end_micros("2024-11") == 1733011200 * 1_000_000  # 2024-12-01
    for bad in ("2024", "2024-13", "2024/12", "nov-2024"):
        with pytest.raises(freeze.FreezeError, match="not a month"):
            freeze.cutoff_end_micros(bad)


def test_manifest_is_deterministic_and_cutoff_aware(conn):
    first = freeze.build_manifest(conn, "2025-01")
    second = freeze.build_manifest(conn, "2025-01")
    assert first["dataset_hash"] == second["dataset_hash"]
    assert first["frozen_at"] <= second["frozen_at"]  # not part of the hash
    assert len(first["files"]) == 5 and len(first["partitions"]) == 3
    assert first["rows_by_interval"] == {"1d": 30 + 31 + 31 + 30 + 31}
    assert first["files"][0] == {
        "key": "data/spot/monthly/klines/BTCBUSD/1d/BTCBUSD-1d-2023-11.zip",
        "size": first["files"][0]["size"],
        "sha256": conn.execute("SELECT verified_checksum FROM archive_files WHERE symbol = 'BTCBUSD' AND month = '2023-11'").fetchone()[0],
    }
    assert [(p["symbol"], p["year"], p["rows"]) for p in first["partitions"]] == [("BTCBUSD", 2023, 61), ("BTCUSDT", 2024, 61), ("BTCUSDT", 2025, 31)]

    earlier = freeze.build_manifest(conn, "2024-11")
    assert earlier["dataset_hash"] != first["dataset_hash"]
    assert [f["key"].rsplit("/", 1)[-1] for f in earlier["files"]] == ["BTCBUSD-1d-2023-11.zip", "BTCBUSD-1d-2023-12.zip", "BTCUSDT-1d-2024-11.zip"]
    assert [(p["symbol"], p["year"], p["rows"]) for p in earlier["partitions"]] == [("BTCBUSD", 2023, 61), ("BTCUSDT", 2024, 30)]
    # The 2024 partition holds November and December; the cutoff keeps November's rows and changes the digest.
    nov = next(p for p in earlier["partitions"] if p["year"] == 2024)
    full = next(p for p in first["partitions"] if p["year"] == 2024)
    assert nov["sha256"] != full["sha256"]


def test_partition_digest_depends_on_values_not_on_file_bytes(conn):
    path = parquet.partition_path("1d", "BTCUSDT", 2025)
    rows, digest = freeze.partition_digest(path, freeze.cutoff_end_micros("2025-01"))
    assert rows == 31
    table = pq.read_table(path)
    rewritten = path.with_name("rewritten.parquet")
    pq.write_table(table, rewritten, compression="snappy", row_group_size=7)  # same rows, different bytes
    assert freeze.partition_digest(rewritten, freeze.cutoff_end_micros("2025-01")) == (31, digest)
    changed = table.set_column(table.schema.get_field_index("close"), "close", pa.array([float(v) + 1e-9 if i == 3 else float(v) for i, v in enumerate(table.column("close").to_pylist())]))
    pq.write_table(changed, rewritten)
    assert freeze.partition_digest(rewritten, freeze.cutoff_end_micros("2025-01"))[1] != digest


def test_write_read_and_tamper_detection(conn, tmp_path: Path):
    manifest = freeze.build_manifest(conn, "2025-01")
    path = freeze.write(manifest)
    assert path == config.manifest_path()
    assert freeze.read(path)["dataset_hash"] == manifest["dataset_hash"]
    text = path.read_text()
    path.write_text(text.replace('"rows": 31', '"rows": 30'))
    with pytest.raises(freeze.FreezeError, match="does not match its own dataset_hash"):
        freeze.read(path)
    (tmp_path / "empty.json").write_text("{}")
    with pytest.raises(freeze.FreezeError, match="has no 'version'"):
        freeze.read(tmp_path / "empty.json")


def test_compare_names_each_difference(conn):
    expected = freeze.build_manifest(conn, "2025-01")
    assert freeze.compare(expected, freeze.build_manifest(conn, "2025-01")) == []
    # A partition rewritten with one different value, and one archive file gone.
    path = parquet.partition_path("1d", "BTCUSDT", 2025)
    table = pq.read_table(path)
    closes = table.column("close").to_pylist()
    closes[0] += 1.0
    pq.write_table(table.set_column(table.schema.get_field_index("close"), "close", pa.array(closes)), path)
    with store.transaction(conn):
        conn.execute("DELETE FROM archive_files WHERE symbol = 'BTCBUSD' AND month = '2023-11'")
    actual = freeze.build_manifest(conn, "2025-01")
    assert freeze.compare(expected, actual) == [
        "file missing here: data/spot/monthly/klines/BTCBUSD/1d/BTCBUSD-1d-2023-11.zip",
        "partition content differs: BTCUSDT 1d 2025 (31 rows expected, 31 here)",
    ]
    assert freeze.compare(expected, freeze.build_manifest(conn, "2024-12"))[0] == "cutoff differs: 2025-01 vs 2024-12"


def test_restore_reproduces_the_hash_on_a_second_machine(conn, monkeypatch, tmp_path: Path):
    """Copy raw/ and the manifest somewhere else, register, verify offline, convert, freeze: same hash."""
    manifest = freeze.build_manifest(conn, "2025-01")
    freeze.write(manifest)
    source = config.data_dir()
    conn.close()

    other = tmp_path / "second-machine"
    monkeypatch.setenv(config.ENV_DATA_DIR, str(other))
    config.ensure_dirs()
    shutil.copytree(source / "raw", other / "raw", dirs_exist_ok=True)
    (other / "raw" / "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2025-01.zip").unlink()  # one file did not make it
    second, _ = store.init()
    restored = freeze.restore(second, freeze.read(source / "manifest.json"))
    assert (restored.files, restored.on_disk, restored.symbols) == (5, 4, 2)
    statuses = {r["month"]: r["status"] for r in second.execute("SELECT month, status FROM archive_files WHERE symbol = 'BTCUSDT'").fetchall()}
    assert statuses == {"2024-11": "downloaded", "2024-12": "downloaded", "2025-01": "pending"}
    row = second.execute("SELECT first_month, last_month, base_asset FROM symbols WHERE symbol = 'BTCUSDT'").fetchone()
    assert (row["first_month"], row["last_month"], row["base_asset"]) == ("2024-11", "2025-01", "BTC")

    # The missing zip gets fetched (here from a fake bucket), then everything verifies against the manifest's digests, offline.
    import httpx

    body = month_zip("BTCUSDT", 2025, 1, 31, "us", False, None)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body if request.url.path.endswith(".zip") else b"")))
    fetched = fetch.fetch_pending(second, client=client)
    assert fetched.downloaded == 1
    verified = checksum.verify_downloaded(second, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert verified.verified == 5 and verified.failed == []
    assert (verified.from_manifest, verified.from_disk, verified.fetched) == (5, 0, 0)
    parquet.convert_verified(second)
    again = freeze.build_manifest(second, "2025-01")
    assert again["dataset_hash"] == manifest["dataset_hash"]
    assert freeze.compare(manifest, again) == []
    second.close()


def test_cli_freeze_restore_and_verify(conn, monkeypatch, tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["data", "freeze", "--cutoff", "2025-01"])
    assert result.exit_code == 0, result.output
    assert "ok      5 files, 3 partitions, 153 rows up to 2025-01; dataset hash " in result.output
    stored = conn.execute("SELECT version, cutoff_month, dataset_hash, file_count, rows_by_interval FROM dataset_manifest").fetchone()
    manifest = freeze.read(config.manifest_path())
    assert (stored["version"], stored["cutoff_month"], stored["dataset_hash"], stored["file_count"]) == ("2025-01", "2025-01", manifest["dataset_hash"], 5)
    assert json.loads(stored["rows_by_interval"]) == {"1d": 153}

    result = runner.invoke(app, ["data", "freeze", "--cutoff", "2025-01", "--verify"])
    assert result.exit_code == 0, result.output
    assert "ok      reproduces" in result.output and "manifest.json" in result.output

    result = runner.invoke(app, ["data", "freeze", "--cutoff", "2024-12", "--verify"])
    assert result.exit_code == 1, result.output
    assert "DIFF  cutoff differs: 2025-01 vs 2024-12" in result.output
    assert "FAILED  verifying the dataset" in result.output

    result = runner.invoke(app, ["data", "freeze", "--cutoff", "last-month"])
    assert result.exit_code == 1 and "why: cutoff 'last-month' is not a month (YYYY-MM)" in result.output

    manifest_path = config.manifest_path()
    other = tmp_path / "elsewhere"
    monkeypatch.setenv(config.ENV_DATA_DIR, str(other))
    config.ensure_dirs()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["data", "restore", str(manifest_path)])
    assert result.exit_code == 0, result.output
    assert "ok      5 files registered (0 already in raw/, 5 to fetch), 2 pairs; next: `lastro data verify`" in result.output
    assert "--verify --manifest" in result.output
    result = runner.invoke(app, ["data", "restore", str(tmp_path / "missing.json")])
    assert result.exit_code == 1 and "FAILED  restoring from the manifest" in result.output
