"""Order book sampler: depth profile arithmetic, files per flush, failures, and a clean interrupt."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import orderbook
from lastro.db import store


def book(mid: float, tick: float, levels: int, qty: float = 1.0) -> dict:
    """A symmetric book: `levels` price levels per side, `qty` units each, starting half a tick from mid."""
    bids = [[f"{mid - tick / 2 - i * tick:.8f}", f"{qty:.8f}"] for i in range(levels)]
    asks = [[f"{mid + tick / 2 + i * tick:.8f}", f"{qty:.8f}"] for i in range(levels)]
    return {"lastUpdateId": 42, "bids": bids, "asks": asks}


BOOKS = {
    "BTCUSDT": book(100_000.0, 0.01, 1000),    # 1000 levels of 1 cent reach only 5 bps from mid
    "ALTUSDT": book(1.0, 0.0001, 1000, qty=10),  # 1000 levels of 1 bp each reach 1000 bps
}


def depth_transport(fail: set[str] = frozenset(), calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/depth"
        symbol = request.url.params["symbol"]
        if calls is not None:
            calls.append((symbol, int(request.url.params["limit"])))
        if symbol in fail:
            return httpx.Response(429, text="slow down")
        if symbol not in BOOKS:
            return httpx.Response(400, text='{"code":-1121,"msg":"Invalid symbol."}')
        return httpx.Response(200, json=BOOKS[symbol])

    return httpx.MockTransport(handler)


@pytest.fixture
def data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    store.init()[0].close()
    return config.data_dir()


def ticking_clock(start: datetime, step: timedelta):
    current = [start - step]

    def now() -> datetime:
        current[0] += step
        return current[0]

    return now


def test_profile_computes_touch_spread_and_bucketed_depth():
    at = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    row = orderbook.profile("BTCUSDT", BOOKS["BTCUSDT"], at)
    assert (row["best_bid"], row["best_ask"], row["mid"]) == (99999.995, 100000.005, 100000.0)
    assert row["spread_bps"] == pytest.approx(0.001)
    assert row["bid_levels"] == row["ask_levels"] == 1000
    assert row["bid_covered_bps"] == pytest.approx(1.0, rel=0.01)  # a thousand cents on 100k is one basis point
    # 1 bp = 10 USDT of price = the whole thousand levels, 1 BTC each: about 1000 * 100k of quote.
    assert row["bid_depth_1bps"] == pytest.approx(1000 * 100_000.0, rel=0.01)
    assert row["bid_depth_100bps"] == row["bid_depth_10bps"] == row["bid_depth_5bps"]  # truncated at what the book reaches
    assert row["ask_depth_1bps"] == pytest.approx(row["bid_depth_1bps"], rel=1e-3)

    alt = orderbook.profile("ALTUSDT", BOOKS["ALTUSDT"], at)
    assert alt["spread_bps"] == pytest.approx(1.0)
    assert alt["bid_covered_bps"] == pytest.approx(1000, rel=0.01)
    assert alt["bid_depth_10bps"] == pytest.approx(sum((1 - 0.00005 - i * 0.0001) * 10 for i in range(10)), rel=1e-6)
    assert alt["bid_depth_100bps"] > alt["bid_depth_50bps"] > alt["bid_depth_10bps"]


def test_profile_rejects_empty_or_crossed_books():
    at = datetime(2026, 10, 17, tzinfo=timezone.utc)
    with pytest.raises(orderbook.OrderBookError, match="empty"):
        orderbook.profile("X", {"bids": [], "asks": [["1", "1"]]}, at)
    with pytest.raises(orderbook.OrderBookError, match="crossed"):
        orderbook.profile("X", {"bids": [["2", "1"]], "asks": [["1", "1"]]}, at)


def test_collect_writes_one_file_per_flush_and_summarizes(data_dir):
    calls: list = []
    sleeps: list = []
    start = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
    result = orderbook.collect(
        ["BTCUSDT", "ALTUSDT"], rounds=3, every=60, flush_every=2,
        client=httpx.Client(transport=depth_transport(calls=calls)),
        sleep=sleeps.append, now=ticking_clock(start, timedelta(seconds=20)),
    )
    assert (result.rounds, result.snapshots, result.failed) == (3, 6, [])
    assert calls[0] == ("BTCUSDT", 1000) and len(calls) == 6
    assert len(sleeps) == 2 and all(0 < s <= 60 for s in sleeps)  # no sleep after the last round
    # Flush after round 2 and the final flush after round 3: one file per symbol per flush, named by its first snapshot.
    files = sorted(p.relative_to(orderbook.orderbook_dir()).as_posix() for p in orderbook.orderbook_dir().rglob("*.parquet"))
    assert result.files == len(files) == 4
    assert files == [
        "symbol=ALTUSDT/date=2026-10-17/120020.parquet",
        "symbol=ALTUSDT/date=2026-10-17/120140.parquet",
        "symbol=BTCUSDT/date=2026-10-17/120000.parquet",
        "symbol=BTCUSDT/date=2026-10-17/120120.parquet",
    ]
    table = pq.read_table(orderbook.orderbook_dir() / "symbol=BTCUSDT/date=2026-10-17/120000.parquet")
    assert table.schema.equals(orderbook.SCHEMA)
    assert table.num_rows == 2
    assert table.column("captured_at")[0].as_py() == start

    btc, alt = result.summary["BTCUSDT"], result.summary["ALTUSDT"]
    assert (btc.snapshots, btc.mid) == (3, 100000.0)
    assert alt.bid_depth_50bps == pytest.approx(sum((1 - 0.00005 - i * 0.0001) * 10 for i in range(50)), rel=1e-6)
    assert btc.spread_bps == pytest.approx(0.001) and btc.covered_bps == pytest.approx(1.0, rel=0.01)
    assert alt.covered_bps == pytest.approx(1000, rel=0.01)


def test_collect_records_failures_and_keeps_going(data_dir):
    result = orderbook.collect(
        ["BTCUSDT", "NOPEUSDT", "ALTUSDT"], rounds=2, every=0,
        client=httpx.Client(transport=depth_transport(fail={"ALTUSDT"})), sleep=lambda s: None,
    )
    assert result.snapshots == 2 and "ALTUSDT" not in result.summary and "BTCUSDT" in result.summary
    assert sorted(set(result.failed)) == [("ALTUSDT", "HTTP 429, rate limited; lower the number of pairs or raise --every"), ("NOPEUSDT", "HTTP 400")]


def test_collect_flushes_on_interrupt(data_dir):
    def interrupt(message: str) -> None:
        if message.startswith("round 2"):
            raise KeyboardInterrupt

    result = orderbook.collect(
        ["BTCUSDT"], rounds=None, every=0, client=httpx.Client(transport=depth_transport()),
        sleep=lambda s: None, progress=interrupt,
    )
    assert result.rounds == 2 and result.snapshots == 2 and result.files == 1
    assert sum(pq.read_table(p).num_rows for p in orderbook.orderbook_dir().rglob("*.parquet")) == 2


def test_cli_collect_orderbook(data_dir, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real(transport=depth_transport()))
    monkeypatch.setattr(orderbook.time, "sleep", lambda s: None)  # collect looks sleep up at call time
    result = CliRunner().invoke(app, ["collect", "orderbook", "-s", "btcusdt", "-s", "ALTUSDT", "--minutes", "2", "--every", "60"])
    assert result.exit_code == 0, result.output
    assert "  2 pairs every 60 s, 2 rounds; writing to" in result.output
    assert "  BTCUSDT: 2 snapshots, mid 100000, spread 0.001 bps; quote within 10 bps of mid: 100.00M bid / 100.00M ask; within 50 bps: 100.00M / 100.00M; book reaches 1.0 bps" in result.output
    assert "  ALTUSDT: 2 snapshots, mid 1, spread 1.000 bps; quote within 10 bps of mid: 100 bid / 100 ask; within 50 bps: 499 / 501; book reaches 999.5 bps" in result.output
    assert "ok      4 snapshots of 2 pairs in 2 rounds, 2 files," in result.output

    result = CliRunner().invoke(app, ["collect", "orderbook", "-s", "NOPEUSDT", "--minutes", "1"])
    assert result.exit_code == 1, result.output
    assert "FAIL  NOPEUSDT: HTTP 400" in result.output
    assert "why: 1 requests failed" in result.output
