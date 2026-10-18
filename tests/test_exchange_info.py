"""exchangeInfo snapshots: parsing, storage, alive_today refresh, and the geo-block message."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import exchange_info
from lastro.db import store

FIXTURE = Path(__file__).parent / "fixtures" / "exchange_info.json"


def transport(status: int = 200, body: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(status, text=body if body is not None else FIXTURE.read_text())

    return httpx.MockTransport(handler)


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()
    with store.transaction(connection):
        for symbol in ("BTCUSDT", "LUNAUSDT", "BTCBUSD"):  # archive pairs; BTCBUSD is gone from the exchange
            connection.execute(
                "INSERT INTO symbols (symbol, base_asset, quote_asset, first_month, last_month, alive_today, updated_at) "
                "VALUES (?, ?, 'USDT', '2020-01', '2026-09', 1, 'x')",
                (symbol, symbol),
            )
    yield connection
    connection.close()


def test_parse_reads_filters_and_tolerates_missing_ones():
    infos = {i.symbol: i for i in exchange_info.parse(json.loads(FIXTURE.read_text()))}
    btc = infos["BTCUSDT"]
    assert (btc.status, btc.base_asset, btc.quote_asset, btc.spot_allowed) == ("TRADING", "BTC", "USDT", True)
    assert (btc.tick_size, btc.step_size, btc.min_qty, btc.min_notional) == (0.01, 0.00001, 0.00001, 5.0)
    assert infos["OLDUSDT"].min_notional == 10.0  # older MIN_NOTIONAL filter
    assert infos["OLDUSDT"].min_qty == 1.0
    eth = infos["ETHBTC"]
    assert (eth.tick_size, eth.step_size, eth.min_qty, eth.min_notional) == (None, None, None, None)


def test_snapshot_stores_rows_raw_json_and_refreshes_alive(conn):
    now = datetime(2026, 10, 17, 13, 45, tzinfo=timezone.utc)
    result = exchange_info.snapshot(conn, client=httpx.Client(transport=transport()), now=now)
    assert (result.captured_on, result.symbols, result.trading, result.usdt_trading, result.alive) == ("2026-10-17", 4, 3, 2, 1)
    assert result.path == config.raw_dir() / "exchange_info" / "2026-10-17.json"
    assert json.loads(result.path.read_text())["symbols"][0]["symbol"] == "BTCUSDT"

    rows = {r["symbol"]: r for r in conn.execute("SELECT * FROM exchange_symbols").fetchall()}
    assert len(rows) == 4
    assert rows["BTCUSDT"]["min_notional"] == 5.0 and rows["BTCUSDT"]["captured_at"] == "2026-10-17T13:45:00+00:00"
    assert rows["LUNAUSDT"]["status"] == "BREAK"

    alive = {r["symbol"]: (r["alive_today"], r["base_asset"]) for r in conn.execute("SELECT symbol, alive_today, base_asset FROM symbols").fetchall()}
    assert alive == {"BTCUSDT": (1, "BTC"), "LUNAUSDT": (0, "LUNA"), "BTCBUSD": (0, "BTCBUSD")}
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 3  # exchange-only symbols are not added


def test_snapshot_same_day_replaces(conn):
    now = datetime(2026, 10, 17, 13, 45, tzinfo=timezone.utc)
    exchange_info.snapshot(conn, client=httpx.Client(transport=transport()), now=now)
    exchange_info.snapshot(conn, client=httpx.Client(transport=transport()), now=now.replace(hour=20))
    assert conn.execute("SELECT COUNT(*) FROM exchange_symbols").fetchone()[0] == 4
    assert exchange_info.latest(conn, "BTCUSDT")["captured_at"] == "2026-10-17T20:45:00+00:00"
    assert exchange_info.latest(conn, "NOPEUSDT") is None


def test_fetch_explains_region_block_and_bad_payloads(monkeypatch):
    monkeypatch.setenv(config.ENV_BINANCE_API, "https://blocked.example")
    with pytest.raises(exchange_info.ExchangeInfoError, match="refuses this region.*LASTRO_BINANCE_API"):
        exchange_info.fetch(httpx.Client(transport=transport(451)))
    with pytest.raises(exchange_info.ExchangeInfoError, match="HTTP 503"):
        exchange_info.fetch(httpx.Client(transport=transport(503)))
    with pytest.raises(exchange_info.ExchangeInfoError, match="did not return JSON"):
        exchange_info.fetch(httpx.Client(transport=transport(200, "<html>")))
    with pytest.raises(exchange_info.ExchangeInfoError, match="no 'symbols' list"):
        exchange_info.fetch(httpx.Client(transport=transport(200, '{"ok": true}')))


def test_cli_exchange_info(conn, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real(transport=transport()))
    result = CliRunner().invoke(app, ["data", "exchange-info"])
    assert result.exit_code == 0, result.output
    assert "ok      4 symbols in the snapshot, 3 trading (2 USDT pairs); 1 archive pairs alive today; saved" in result.output
    assert conn.execute("SELECT outcome FROM runs").fetchone()["outcome"] == "ok"

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real(transport=transport(451)))
    result = CliRunner().invoke(app, ["data", "exchange-info"])
    assert result.exit_code == 1
    assert "FAILED  capturing exchangeInfo" in result.output
    assert "fix: check the connection" in result.output and "LASTRO_BINANCE_API" in result.output


def test_commands_refuse_a_database_behind_the_code(conn, monkeypatch):
    """A migration the database has not applied yet stops every command with the same instruction."""
    real = store._migration_files
    extra = (999, "999_future.sql", "CREATE TABLE future (id INTEGER PRIMARY KEY);")
    monkeypatch.setattr(store, "_migration_files", lambda: real() + [extra])
    result = CliRunner().invoke(app, ["data", "status"])
    assert result.exit_code == 1, result.output
    assert "why: schema is behind the code: 999_future.sql not applied" in result.output
    assert "fix: run `lastro db init` once; existing data is kept" in result.output

    result = CliRunner().invoke(app, ["db", "init"])
    assert result.exit_code == 0, result.output
    assert "applied 999_future.sql" in result.output
    assert CliRunner().invoke(app, ["data", "status"]).exit_code == 0
