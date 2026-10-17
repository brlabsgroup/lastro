"""Download and checksum tests. No network: a MockTransport serves small real zips."""

import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import archive, checksum, fetch
from lastro.db import store


def make_zip(symbol: str, month: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{symbol}-1d-{month}.csv", f"open_time,open\n1,{len(month)}\n")
    return buf.getvalue()


FILES = {
    ("BTCUSDT", "1d", "2021-01"): make_zip("BTCUSDT", "2021-01"),
    ("BTCUSDT", "1d", "2021-02"): make_zip("BTCUSDT", "2021-02"),
    ("BTCBUSD", "1d", "2023-06"): make_zip("BTCBUSD", "2023-06"),
}
BAD_CHECKSUM_FOR = ("BTCUSDT", "1d", "2021-02")  # the bucket "publishes" a wrong hash for this one


def url_for(symbol: str, interval: str, month: str) -> str:
    return f"{archive.BASE_URL}/{archive.KLINES_PREFIX}{symbol}/{interval}/{symbol}-{interval}-{month}.zip"


def fake_transport(
    fail_key: tuple[str, str, str] | None = None,
    truncate_key: tuple[str, str, str] | None = None,
    checksum_fail_key: tuple[str, str, str] | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for key, body in FILES.items():
            symbol, interval, month = key
            name = f"{symbol}-{interval}-{month}.zip"
            if path.endswith(name + ".CHECKSUM"):
                if key == checksum_fail_key:
                    return httpx.Response(503, text="try later")
                digest = hashlib.sha256(body).hexdigest()
                if key == BAD_CHECKSUM_FOR:
                    digest = "0" * 64
                return httpx.Response(200, text=f"{digest}  {name}\n")
            if path.endswith(name):
                if key == fail_key:
                    return httpx.Response(503, text="try later")
                if key == truncate_key:
                    return httpx.Response(200, content=body[: len(body) // 2])
                return httpx.Response(200, content=body)
        return httpx.Response(404, text="no such key")

    return httpx.MockTransport(handler)


def no_network():
    """A client that fails every request, to prove a step needs none."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"network used for {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()
    with store.transaction(connection):
        for (symbol, interval, month), body in FILES.items():
            connection.execute(
                "INSERT INTO archive_files (symbol, interval, month, url, size_bytes, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                (symbol, interval, month, url_for(symbol, interval, month), len(body)),
            )
    yield connection
    connection.close()


def statuses(conn) -> dict[tuple[str, str, str], str]:
    rows = conn.execute("SELECT symbol, interval, month, status FROM archive_files").fetchall()
    return {(r["symbol"], r["interval"], r["month"]): r["status"] for r in rows}


# --- fetch -----------------------------------------------------------------------------------------


def test_key_and_local_path(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path))
    url = url_for("BTCUSDT", "1d", "2021-01")
    assert fetch.key_from_url(url) == "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2021-01.zip"
    assert fetch.local_path(url) == tmp_path / "raw" / "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2021-01.zip"
    with pytest.raises(fetch.FetchError):
        fetch.key_from_url("https://elsewhere.example/x.zip")


def test_fetch_downloads_pending_and_marks_downloaded(conn):
    client = httpx.Client(transport=fake_transport())
    result = fetch.fetch_pending(conn, client=client)
    assert result.downloaded == 3 and result.skipped == 0 and result.failed == []
    assert result.checksums == 3
    assert all(status == "downloaded" for status in statuses(conn).values())
    url = url_for("BTCUSDT", "1d", "2021-01")
    path = fetch.local_path(url)
    assert path.exists() and path.read_bytes() == FILES[("BTCUSDT", "1d", "2021-01")]
    assert not path.with_suffix(".zip.part").exists()
    companion = fetch.checksum_path(url)
    assert companion == path.parent / "BTCUSDT-1d-2021-01.zip.CHECKSUM"
    assert companion.read_text().split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_fetch_takes_daily_files_first(conn):
    """1d for every pair comes before any 4h, and 4h before any 1h, so the daily dataset completes first."""
    with store.transaction(conn):
        for symbol, interval in (("BTCUSDT", "1h"), ("BTCUSDT", "4h"), ("AAAUSDT", "1h"), ("AAAUSDT", "1d")):
            conn.execute(
                "INSERT INTO archive_files (symbol, interval, month, url, size_bytes, status) VALUES (?, ?, '2021-01', ?, 1, 'pending')",
                (symbol, interval, url_for(symbol, interval, "2021-01")),
            )
    rows = fetch._select_pending(conn, None, None)
    assert [(r["interval"], r["symbol"]) for r in rows] == [
        ("1d", "AAAUSDT"), ("1d", "BTCBUSD"), ("1d", "BTCUSDT"), ("1d", "BTCUSDT"),
        ("4h", "BTCUSDT"),
        ("1h", "AAAUSDT"), ("1h", "BTCUSDT"),
    ]


def test_fetch_missing_checksum_is_not_a_failure(conn):
    """The zip is the job. Without its companion, verify fetches the digest over the network instead."""
    key = ("BTCBUSD", "1d", "2023-06")
    client = httpx.Client(transport=fake_transport(checksum_fail_key=key))
    result = fetch.fetch_pending(conn, client=client)
    assert result.downloaded == 3 and result.failed == [] and result.checksums == 2
    assert statuses(conn)[key] == "downloaded"
    assert not fetch.checksum_path(url_for(*key)).exists()

    verified = checksum.verify_downloaded(conn, client=httpx.Client(transport=fake_transport()))
    assert verified.verified == 2 and verified.from_disk == 2 and verified.fetched == 1


def test_fetch_resume_fills_in_missing_checksums(conn):
    """Zips downloaded before the companions existed get theirs on the next run, without re-downloading the zip."""
    client = httpx.Client(transport=fake_transport())
    fetch.fetch_pending(conn, client=client)
    url = url_for("BTCUSDT", "1d", "2021-01")
    fetch.checksum_path(url).unlink()
    with store.transaction(conn):
        conn.execute("UPDATE archive_files SET status = 'pending'")
    again = fetch.fetch_pending(conn, client=client)
    assert again.downloaded == 0 and again.skipped == 3 and again.checksums == 3
    assert fetch.checksum_path(url).exists()


def test_duration_and_eta():
    assert fetch.duration(42) == "42 s"
    assert fetch.duration(61) == "1 min"
    assert fetch.duration(3 * 3600 + 7 * 60) == "3 h 07 min"
    assert fetch.eta(0, 100, 0) == "unknown"
    assert fetch.eta(500, 84705, 100) == "4 h 40 min"  # 5 files/s, 84205 to go


def test_fetch_interrupt_propagates_without_hanging(conn):
    def interrupt(_message: str) -> None:
        raise KeyboardInterrupt

    client = httpx.Client(transport=fake_transport())
    with pytest.raises(KeyboardInterrupt):
        fetch.fetch_pending(conn, client=client, progress=interrupt)


def test_fetch_second_run_downloads_nothing(conn):
    client = httpx.Client(transport=fake_transport())
    fetch.fetch_pending(conn, client=client)
    again = fetch.fetch_pending(conn, client=client)
    assert again.downloaded == 0 and again.skipped == 0  # nothing pending any more


def test_fetch_resumes_files_already_on_disk(conn):
    """A file present with the right size (e.g. from an interrupted earlier run) is not re-downloaded."""
    url = url_for("BTCBUSD", "1d", "2023-06")
    path = fetch.local_path(url)
    path.parent.mkdir(parents=True)
    path.write_bytes(FILES[("BTCBUSD", "1d", "2023-06")])
    client = httpx.Client(transport=fake_transport())
    result = fetch.fetch_pending(conn, client=client)
    assert result.downloaded == 2 and result.skipped == 1


def test_fetch_rejects_truncated_download(conn):
    key = ("BTCUSDT", "1d", "2021-01")
    client = httpx.Client(transport=fake_transport(truncate_key=key))
    result = fetch.fetch_pending(conn, client=client)
    assert result.downloaded == 2
    assert len(result.failed) == 1 and "size mismatch" in result.failed[0][1]
    assert statuses(conn)[key] == "pending"
    assert not fetch.local_path(url_for(*key)).exists()
    assert not fetch.local_path(url_for(*key)).with_suffix(".zip.part").exists()


def test_fetch_collects_http_failures(conn):
    key = ("BTCBUSD", "1d", "2023-06")
    client = httpx.Client(transport=fake_transport(fail_key=key))
    result = fetch.fetch_pending(conn, client=client)
    assert result.downloaded == 2
    assert result.failed == [(fetch.key_from_url(url_for(*key)), "HTTP 503")]
    assert statuses(conn)[key] == "pending"


def test_fetch_filters_by_symbol_and_interval(conn):
    client = httpx.Client(transport=fake_transport())
    result = fetch.fetch_pending(conn, symbols=["BTCBUSD"], intervals=("1d",), client=client)
    assert result.downloaded == 1
    assert statuses(conn)[("BTCUSDT", "1d", "2021-01")] == "pending"


# --- verify ----------------------------------------------------------------------------------------


def test_parse_checksum_file():
    digest = "a" * 64
    assert checksum.parse_checksum_file(f"{digest}  X-1d-2021-01.zip\n", "X-1d-2021-01.zip") == digest
    assert checksum.parse_checksum_file(f"{digest} *X-1d-2021-01.zip", "X-1d-2021-01.zip") == digest
    with pytest.raises(checksum.ChecksumError):
        checksum.parse_checksum_file("garbage", "X-1d-2021-01.zip")
    with pytest.raises(checksum.ChecksumError):
        checksum.parse_checksum_file(f"{digest}  OTHER.zip", "X-1d-2021-01.zip")


def test_verify_marks_good_files_and_requeues_bad_ones(conn):
    """Runs with no network at all: the digests come from the companions fetch stored."""
    fetch.fetch_pending(conn, client=httpx.Client(transport=fake_transport()))
    result = checksum.verify_downloaded(conn, client=no_network())
    assert result.verified == 2 and result.from_disk == 3 and result.fetched == 0
    assert len(result.failed) == 1 and "hash mismatch" in result.failed[0][1]
    final = statuses(conn)
    assert final[("BTCUSDT", "1d", "2021-01")] == "verified"
    assert final[("BTCBUSD", "1d", "2023-06")] == "verified"
    assert final[BAD_CHECKSUM_FOR] == "pending"
    assert not fetch.local_path(url_for(*BAD_CHECKSUM_FOR)).exists()
    assert not fetch.checksum_path(url_for(*BAD_CHECKSUM_FOR)).exists()  # both go, both come back on the next fetch
    row = conn.execute(
        "SELECT expected_checksum, verified_checksum FROM archive_files WHERE symbol = 'BTCUSDT' AND month = '2021-01'"
    ).fetchone()
    assert row["expected_checksum"] == row["verified_checksum"] == hashlib.sha256(FILES[("BTCUSDT", "1d", "2021-01")]).hexdigest()


def test_verify_without_companion_and_without_network_fails_clearly(conn):
    fetch.fetch_pending(conn, symbols=["BTCBUSD"], client=httpx.Client(transport=fake_transport()))
    fetch.checksum_path(url_for("BTCBUSD", "1d", "2023-06")).unlink()
    result = checksum.verify_downloaded(conn, client=no_network())
    assert result.verified == 0 and len(result.failed) == 1
    assert "no .CHECKSUM on disk and could not fetch it" in result.failed[0][1]
    assert statuses(conn)[("BTCBUSD", "1d", "2023-06")] == "downloaded"  # nothing deleted, nothing re-queued


def test_verify_detects_corruption_on_disk(conn):
    """The canonical acceptance test: alter a downloaded zip by hand, verify must catch exactly that one."""
    client = httpx.Client(transport=fake_transport())
    fetch.fetch_pending(conn, client=client)
    victim = fetch.local_path(url_for("BTCBUSD", "1d", "2023-06"))
    victim.write_bytes(victim.read_bytes() + b"corrupted")
    result = checksum.verify_downloaded(conn, symbols=["BTCBUSD"], client=client)
    assert result.verified == 0 and len(result.failed) == 1
    assert statuses(conn)[("BTCBUSD", "1d", "2023-06")] == "pending"
    assert not victim.exists()


def test_verify_skips_already_verified(conn):
    client = httpx.Client(transport=fake_transport())
    fetch.fetch_pending(conn, symbols=["BTCBUSD"], client=client)
    checksum.verify_downloaded(conn, symbols=["BTCBUSD"], client=client)
    again = checksum.verify_downloaded(conn, symbols=["BTCBUSD"], client=client)
    assert again.verified == 0 and again.failed == []


# --- CLI -------------------------------------------------------------------------------------------


def _fake_client_factory(transport):
    real = httpx.Client
    return lambda *a, **k: real(transport=transport)


def test_cli_fetch_then_verify(conn, monkeypatch):
    monkeypatch.setattr(httpx, "Client", _fake_client_factory(fake_transport()))
    runner = CliRunner()
    result = runner.invoke(app, ["data", "fetch", "-s", "BTCUSDT", "-s", "BTCBUSD", "--workers", "2"])
    assert result.exit_code == 0, result.output
    assert "3 files to download, about 0.0 MB, 2 workers" in result.output
    assert "ok      3 downloaded, 0 already on disk, 3/3 .CHECKSUM files, 0.0 MB, 0 s" in result.output

    result = runner.invoke(app, ["data", "verify"])
    assert result.exit_code == 1  # one published checksum is wrong on purpose
    assert "2 verified, 1 failed (checksums: 3 from disk, 0 fetched), 0 s" in result.output
    assert "re-queued" in result.output
    runs = [r["outcome"] for r in conn.execute("SELECT outcome FROM runs ORDER BY id").fetchall()]
    assert runs == ["ok", "partial"]


def test_cli_fetch_interrupted_says_how_to_resume(conn, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(fetch, "fetch_pending", interrupted)
    result = CliRunner().invoke(app, ["data", "fetch"])
    assert result.exit_code == 1, result.output
    assert "why: interrupted" in result.output
    assert "fix: run `lastro data fetch` again: it resumes where it stopped" in result.output
    assert conn.execute("SELECT outcome FROM runs").fetchone()["outcome"] == "interrupted"
