"""Currency rates: PTAX from a fake Central Bank API, USDTBRL from a synthetic Parquet, lookups across weekends."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import fx, parquet
from lastro.db import store


def business_days(start: date, end: date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def bcb_transport(calls: list | None = None, status: int = 200):
    """Serves series 1 (sell) and 10813 (buy) for business days in the requested range, like the SGS API."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url)
        if status != 200:
            return httpx.Response(status, text="unavailable")
        series = request.url.path.split("bcdata.sgs.")[1].split("/")[0]
        start = datetime.strptime(request.url.params["dataInicial"], "%d/%m/%Y").date()
        end = datetime.strptime(request.url.params["dataFinal"], "%d/%m/%Y").date()
        base = 5.0 if series == "1" else 4.99
        rows = [{"data": d.strftime("%d/%m/%Y"), "valor": f"{base + d.timetuple().tm_yday / 1000:.4f}"} for d in business_days(start, end)]
        return httpx.Response(200, text=json.dumps(rows))

    return httpx.MockTransport(handler)


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()
    yield connection
    connection.close()


def test_fetch_series_chunks_by_year_and_parses_rows():
    calls: list = []
    rows = fx.fetch_series(httpx.Client(transport=bcb_transport(calls)), 1, date(2024, 12, 30), date(2025, 1, 3))
    assert [str(c.params["dataInicial"]) + ".." + str(c.params["dataFinal"]) for c in calls] == ["30/12/2024..31/12/2024", "01/01/2025..03/01/2025"]
    assert [d.isoformat() for d, _ in rows] == ["2024-12-30", "2024-12-31", "2025-01-01", "2025-01-02", "2025-01-03"]
    assert rows[0][1] == pytest.approx(5.0 + 365 / 1000)


def test_update_ptax_fills_then_continues_from_the_last_day(conn):
    first = fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport()), start=date(2026, 1, 1), end=date(2026, 1, 10))
    assert first.ptax_rows == 2 * 7  # 7 business days, two series
    assert (first.first, first.last) == ("2026-01-01", "2026-01-09")

    calls: list = []
    second = fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport(calls)), end=date(2026, 1, 15))
    assert all(str(c.params["dataInicial"]) == "10/01/2026" for c in calls)  # resumes the day after the last stored one
    assert second.ptax_rows == 2 * 4
    assert second.last == "2026-01-15"
    assert conn.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 22

    third = fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport()), end=date(2026, 1, 15))
    assert third.ptax_rows == 0  # up to date, nothing fetched


def test_empty_ranges_answer_404_and_mean_no_fixing(conn):
    """The SGS API answers 404 for a weekend or for today before the fixing: nothing to add, not an error."""
    fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport()), start=date(2026, 1, 1), end=date(2026, 1, 9))
    messages: list = []
    result = fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport(status=404)), end=date(2026, 1, 10), progress=messages.append)
    assert result.ptax_rows == 0 and result.last == "2026-01-09"
    assert messages == ["ptax_sell: 0 days from 10/01/2026 to 10/01/2026 (no fixing published yet)", "ptax_buy: 0 days from 10/01/2026 to 10/01/2026 (no fixing published yet)"]


def test_update_ptax_reports_api_failures(conn):
    with pytest.raises(fx.FxError, match="HTTP 503"):
        fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport(status=503)), start=date(2026, 1, 1), end=date(2026, 1, 2))


def test_rate_on_resolves_weekends_to_the_previous_business_day(conn):
    fx.update_ptax(conn, client=httpx.Client(transport=bcb_transport()), start=date(2026, 1, 1), end=date(2026, 1, 10))
    assert fx.rate_on(conn, date(2026, 1, 9))[0] == date(2026, 1, 9)  # Friday
    assert fx.rate_on(conn, "2026-01-11")[0] == date(2026, 1, 9)  # Sunday -> Friday
    assert fx.rate_on(conn, date(2026, 1, 11), source="ptax_buy")[1] == pytest.approx(4.99 + 9 / 1000)
    assert fx.rate_on(conn, date(2026, 2, 1)) is None  # beyond the lookback window
    assert fx.rate_on(conn, date(2025, 12, 31)) is None


def test_update_usdtbrl_reads_daily_closes(conn):
    rows = []
    for n in range(5):
        at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=n)
        rows.append({
            "symbol": "USDTBRL", "open_time": at, "open": 5.4, "high": 5.5, "low": 5.3, "close": 5.4 + n / 100,
            "volume": 0.0 if n == 4 else 100.0, "quote_volume": 540.0, "trades": 3,
            "taker_buy_base": 50.0, "taker_buy_quote": 270.0, "source_file": "USDTBRL-1d-2026-01.zip",
        })
    path = parquet.partition_path("1d", "USDTBRL", 2026)
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=parquet.SCHEMA), path)
    assert fx.update_usdtbrl(conn) == 4  # the zero-volume day carries no price
    stored = conn.execute("SELECT date, rate FROM fx_rates WHERE source = 'usdtbrl_close' ORDER BY date").fetchall()
    assert [r["date"] for r in stored] == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert [r["rate"] for r in stored] == pytest.approx([5.4, 5.41, 5.42, 5.43])
    found = fx.rate_on(conn, "2026-01-03", source="usdtbrl_close")
    assert found[0] == date(2026, 1, 3) and found[1] == pytest.approx(5.42)


def test_update_usdtbrl_without_parquet_is_zero(conn):
    assert fx.update_usdtbrl(conn) == 0


def test_cli_fx(conn, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real(transport=bcb_transport()))
    monkeypatch.setattr(fx, "PTAX_START", date(2026, 1, 1))
    result = CliRunner().invoke(app, ["data", "fx"])
    assert result.exit_code == 0, result.output
    assert "  ptax_sell:" in result.output and "  ptax_buy:" in result.output
    assert "PTAX days added, series now 2026-01-01 to" in result.output
    assert "0 USDTBRL closes (none converted yet: run `lastro data list -s USDTBRL -i 1d`" in result.output

    result = CliRunner().invoke(app, ["data", "fx", "--since", "yesterday"])
    assert result.exit_code == 1
    assert "why: --since 'yesterday' is not a date" in result.output

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real(transport=bcb_transport(status=500)))
    result = CliRunner().invoke(app, ["data", "fx", "--since", "2026-01-01"])
    assert result.exit_code == 1
    assert "FAILED  fetching PTAX" in result.output and "HTTP 500" in result.output
