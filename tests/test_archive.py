"""Archive enumeration tests. No network: the S3 bucket is simulated with httpx's MockTransport."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import archive
from lastro.db import store

FIXTURES = Path(__file__).parent / "fixtures"
ROOT_XML = (FIXTURES / "s3_root_listing.xml").read_text(encoding="utf-8")
PAIR_XML = (FIXTURES / "s3_pair_listing.xml").read_text(encoding="utf-8")


def _pair_xml(symbol: str, interval: str, months: list[str]) -> str:
    """Build a pair listing for the fake bucket from a list of months."""
    contents = []
    for month in months:
        key = f"{archive.KLINES_PREFIX}{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
        contents.append(
            f"<Contents><Key>{key}</Key><Size>1000</Size></Contents>"
            f"<Contents><Key>{key}.CHECKSUM</Key><Size>98</Size></Contents>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Prefix>{archive.KLINES_PREFIX}{symbol}/{interval}/</Prefix><IsTruncated>false</IsTruncated>"
        + "".join(contents)
        + "</ListBucketResult>"
    )


FAKE_BUCKET = {
    "BTCUSDT": ["2017-08", "2017-09", "2026-09"],
    "BTCBUSD": ["2019-09", "2023-06"],
    "LUNAUSDT": ["2020-08", "2022-05"],
    "USDTBRL": ["2020-09", "2026-09"],  # BRL-quoted: excluded here, handled by the fx module (piece 7)
}


def fake_transport(fail_symbol: str | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        prefix = params.get("prefix", [""])[0]
        if prefix == archive.KLINES_PREFIX:
            return httpx.Response(200, text=ROOT_XML)
        parts = prefix[len(archive.KLINES_PREFIX):].strip("/").split("/")
        symbol, interval = parts[0], parts[1]
        if symbol == fail_symbol:
            return httpx.Response(503, text="slow down")
        return httpx.Response(200, text=_pair_xml(symbol, interval, FAKE_BUCKET.get(symbol, [])))

    return httpx.MockTransport(handler)


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    connection, _ = store.init()
    yield connection
    connection.close()


def test_parse_root_listing_returns_folders():
    folders, files, marker = archive.parse_listing(ROOT_XML)
    assert len(folders) == 5
    assert "data/spot/monthly/klines/BTCBUSD/" in folders
    assert files == []
    assert marker is None


def test_parse_pair_listing_keeps_only_zips():
    _, files, marker = archive.parse_listing(PAIR_XML)
    assert len(files) == 4  # two zips + two checksums as raw keys
    assert marker is None
    zips = [key for key, _ in files if key.endswith(".zip")]
    assert zips == [
        "data/spot/monthly/klines/LUNAUSDT/1d/LUNAUSDT-1d-2020-08.zip",
        "data/spot/monthly/klines/LUNAUSDT/1d/LUNAUSDT-1d-2022-05.zip",
    ]


def test_parse_rejects_non_xml():
    with pytest.raises(archive.ArchiveError):
        archive.parse_listing("<html>not the bucket</html>")


def test_parse_names_html_pages_explicitly():
    """The archive's website root returns an HTML browser page; the error must say so, not 'mismatched tag'."""
    with pytest.raises(archive.ArchiveError, match="HTML page"):
        archive.parse_listing("<!DOCTYPE html>\n<html><head><title>Binance Data Collection</title></head><body></body></html>")


def test_listing_requests_go_to_the_bucket_endpoint():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=ROOT_XML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    archive.list_symbols(client)
    assert seen and seen[0].startswith(archive.LIST_URL + "?")
    assert "prefix=data%2Fspot%2Fmonthly%2Fklines%2F" in seen[0] or "prefix=data/spot/monthly/klines/" in seen[0]


def test_zip_name_pattern():
    match = archive.ZIP_NAME.match("BTCUSDT-1d-2023-06.zip")
    assert match and match.group("symbol") == "BTCUSDT" and match.group("month") == "2023-06"
    assert archive.ZIP_NAME.match("BTCUSDT-1d-2023-06.zip.CHECKSUM") is None


def test_list_symbols_filters_to_usdt():
    client = httpx.Client(transport=fake_transport())
    # BTCBUSD ends in BUSD, ETHBTC in BTC, USDTBRL in BRL. Only two are USDT-quoted:
    assert archive.list_symbols(client) == ["BTCUSDT", "LUNAUSDT"]


def test_enumerate_writes_files_and_symbols(conn):
    client = httpx.Client(transport=fake_transport())
    result = archive.enumerate_archive(conn, intervals=("1d", "1h"), client=client)
    assert result.pairs == 2
    assert result.files == 2 * (3 + 2)  # two intervals, months per pair
    assert result.failed_pairs == []

    rows = {r["symbol"]: r for r in conn.execute("SELECT * FROM symbols").fetchall()}
    assert rows["LUNAUSDT"]["first_month"] == "2020-08"
    assert rows["LUNAUSDT"]["last_month"] == "2022-05"  # dead pair keeps its last month
    assert rows["BTCUSDT"]["last_month"] == "2026-09"
    assert rows["BTCUSDT"]["base_asset"] == "BTC" and rows["BTCUSDT"]["quote_asset"] == "USDT"
    assert rows["LUNAUSDT"]["last_candle"] is None  # only known after conversion

    files = conn.execute("SELECT * FROM archive_files WHERE symbol = 'LUNAUSDT' ORDER BY interval, month").fetchall()
    assert [(f["interval"], f["month"], f["status"]) for f in files] == [
        ("1d", "2020-08", "pending"),
        ("1d", "2022-05", "pending"),
        ("1h", "2020-08", "pending"),
        ("1h", "2022-05", "pending"),
    ]
    assert files[0]["url"].endswith("LUNAUSDT-1d-2020-08.zip")


def test_enumerate_twice_preserves_download_status(conn):
    client = httpx.Client(transport=fake_transport())
    archive.enumerate_archive(conn, intervals=("1d",), client=client)
    with store.transaction(conn):
        conn.execute(
            "UPDATE archive_files SET status = 'verified', verified_checksum = 'abc' "
            "WHERE symbol = 'BTCUSDT' AND interval = '1d' AND month = '2017-08'"
        )
    result = archive.enumerate_archive(conn, intervals=("1d",), client=client)
    assert result.pairs == 2
    row = conn.execute(
        "SELECT status, verified_checksum FROM archive_files WHERE symbol = 'BTCUSDT' AND interval = '1d' AND month = '2017-08'"
    ).fetchone()
    assert row["status"] == "verified" and row["verified_checksum"] == "abc"
    assert conn.execute("SELECT COUNT(*) AS n FROM archive_files").fetchone()["n"] == 5


def test_enumerate_collects_failures_and_keeps_the_rest(conn):
    client = httpx.Client(transport=fake_transport(fail_symbol="LUNAUSDT"))
    result = archive.enumerate_archive(conn, intervals=("1d",), client=client)
    assert result.pairs == 1
    assert [symbol for symbol, _ in result.failed_pairs] == ["LUNAUSDT"]
    assert "HTTP 503" in result.failed_pairs[0][1]
    assert conn.execute("SELECT COUNT(*) AS n FROM symbols").fetchone()["n"] == 1


def test_enumerate_with_explicit_symbols_skips_root_listing(conn):
    client = httpx.Client(transport=fake_transport())
    result = archive.enumerate_archive(conn, intervals=("1d",), symbols=["BTCUSDT"], client=client)
    assert result.pairs == 1 and result.files == 3


def _fake_client_factory(transport: httpx.MockTransport):
    real_client = httpx.Client
    return lambda *a, **k: real_client(transport=transport)


def test_cli_data_list_records_run(conn, monkeypatch):
    monkeypatch.setattr(httpx, "Client", _fake_client_factory(fake_transport()))
    runner = CliRunner()
    result = runner.invoke(app, ["data", "list", "-i", "1d"])
    assert result.exit_code == 0, result.output
    assert "ok      2 pairs, 5 files listed" in result.output
    run = conn.execute("SELECT command, outcome, detail FROM runs").fetchone()
    assert run["command"] == "data list" and run["outcome"] == "ok" and "5 files" in run["detail"]


def test_cli_data_list_reports_partial_failure(conn, monkeypatch):
    monkeypatch.setattr(httpx, "Client", _fake_client_factory(fake_transport(fail_symbol="BTCUSDT")))
    result = CliRunner().invoke(app, ["data", "list", "-i", "1d"])
    assert result.exit_code == 1
    assert "FAIL  BTCUSDT" in result.output
    assert "fix: run `lastro data list` again" in result.output
    assert conn.execute("SELECT outcome FROM runs").fetchone()["outcome"] == "partial"


def test_split_symbol_knows_the_quote_assets():
    assert archive.split_symbol("BTCUSDT") == ("BTC", "USDT")
    assert archive.split_symbol("BTCBUSD") == ("BTC", "BUSD")
    assert archive.split_symbol("USDTBRL") == ("USDT", "BRL")
    assert archive.split_symbol("BTCFDUSD") == ("BTC", "FDUSD")
    assert archive.split_symbol("ETHBTC") == ("ETH", "BTC")
    assert archive.split_symbol("WEIRDXYZ") == ("WEIRDXYZ", None)


def test_upsert_symbol_derives_base_and_quote_but_defers_to_exchange_info(conn):
    archive._upsert_symbol(conn, "USDTBRL", ["2020-11", "2026-09"])
    archive._upsert_symbol(conn, "FOOUSDT", ["2024-01"])
    rows = {r["symbol"]: r for r in conn.execute("SELECT * FROM symbols").fetchall()}
    assert (rows["USDTBRL"]["base_asset"], rows["USDTBRL"]["quote_asset"]) == ("USDT", "BRL")
    assert (rows["FOOUSDT"]["base_asset"], rows["FOOUSDT"]["quote_asset"]) == ("FOO", "USDT")
    # exchangeInfo knows better (a token whose ticker differs from its pair name); a later listing must not undo it.
    with store.transaction(conn):
        conn.execute("UPDATE symbols SET base_asset = 'FOOX' WHERE symbol = 'FOOUSDT'")
        conn.execute(
            "INSERT INTO exchange_symbols (captured_on, symbol, status, base_asset, quote_asset, spot_allowed, captured_at) "
            "VALUES ('2026-10-17', 'FOOUSDT', 'TRADING', 'FOOX', 'USDT', 1, 't')"
        )
    archive._upsert_symbol(conn, "FOOUSDT", ["2024-02"])
    row = conn.execute("SELECT base_asset, first_month, last_month FROM symbols WHERE symbol = 'FOOUSDT'").fetchone()
    assert (row["base_asset"], row["first_month"], row["last_month"]) == ("FOOX", "2024-01", "2024-02")
