"""Universe: asset types, restrictions, and point-in-time eligibility on synthetic daily data."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.data import parquet
from lastro.db import store
from lastro.universe import assets, build, restrictions

JAN1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAYS = 182  # January 1 to June 30, 2024


def day(n: int) -> datetime:
    return JAN1 + timedelta(days=n)


def write_pair(symbol: str, quote_volume: float, first: int = 0, last: int = DAYS - 1, skip=(), zero=()) -> None:
    rows = []
    for n in range(first, last + 1):
        if n in skip:
            continue
        volume = 0.0 if n in zero else 100.0
        rows.append({
            "symbol": symbol, "open_time": day(n), "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
            "volume": volume, "quote_volume": quote_volume if volume else 0.0, "trades": 5,
            "taker_buy_base": 50.0, "taker_buy_quote": 500.0, "source_file": f"{symbol}-1d-{day(n):%Y-%m}.zip",
        })
    path = parquet.partition_path("1d", symbol, 2024)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=parquet.SCHEMA), path)


PAIRS = {
    # symbol: base asset
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "SMALLUSDT": "SMALL", "BTCUPUSDT": "BTCUP", "JUPUSDT": "JUP",
    "USDCUSDT": "USDC", "NEWUSDT": "NEW", "GAPUSDT": "GAP", "ZEROUSDT": "ZERO", "BTCBUSD": "BTC", "USDTBRL": "USDT",
}
N = {d: (date(2024, 1, 1) + timedelta(days=d)).isoformat() for d in range(DAYS + 1)}
MAR1, APR1, APR20, MAY1 = 60, 91, 110, 121


@pytest.fixture
def conn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    config.ensure_dirs()
    connection, _ = store.init()
    with store.transaction(connection):
        for symbol, base in PAIRS.items():
            connection.execute(
                "INSERT INTO symbols (symbol, base_asset, quote_asset, first_month, last_month, updated_at) VALUES (?, ?, 'USDT', '2024-01', '2024-06', 'x')",
                (symbol, base),
            )
        connection.execute(
            "INSERT INTO quality_issues (symbol, interval, period_start, period_end, check_name, detail, action, detected_at) VALUES "
            "('BTCUSDT', '1d', '2024-04-15T00:00:00+00:00', '2024-04-15T00:00:00+00:00', 'ohlc', 'x', 'row excluded from the universe', 't'), "
            "('ZEROUSDT', '1d', '2024-03-09T00:00:00+00:00', '2024-03-12T00:00:00+00:00', 'zero_volume', 'x', 'pair flagged suspended for the period', 't'), "
            "('ETHUSDT', '1d', '2024-02-01T00:00:00+00:00', '2024-02-03T00:00:00+00:00', 'gap', 'x', 'period flagged for the universe', 't'), "
            "('ETHUSDT', '1h', '2024-05-01T00:00:00+00:00', '2024-05-01T00:00:00+00:00', 'ohlc', 'x', 'row excluded from the universe', 't')"
        )
    write_pair("BTCUSDT", 100e6)
    write_pair("ETHUSDT", 50e6)
    write_pair("SMALLUSDT", 100e3)
    write_pair("BTCUPUSDT", 20e6)
    write_pair("JUPUSDT", 5e6)
    write_pair("USDCUSDT", 500e6)
    write_pair("NEWUSDT", 30e6, first=MAY1)
    write_pair("GAPUSDT", 30e6, skip=range(APR1, APR20 + 1))  # off the tape April 1 to 20: 20 days, longer than 7
    write_pair("ZEROUSDT", 30e6, zero=range(68, 72))  # March 9 to 12 with zero volume
    write_pair("BTCBUSD", 80e6)  # BUSD-quoted duplicate of BTC
    write_pair("USDTBRL", 400e6)  # BRL-quoted fiat leg, volume in BRL
    yield connection
    connection.close()


def universe_row(conn, symbol: str, decision_date: str, jurisdiction: str = ""):
    return conn.execute(
        "SELECT * FROM universe WHERE jurisdiction = ? AND symbol = ? AND decision_date = ?", (jurisdiction, symbol, decision_date)
    ).fetchone()


# --- asset types ---------------------------------------------------------------------------------


def test_asset_types():
    known = {"BTC", "ETH", "SUSHI", "1INCH", "JUP", "ADA"}
    assert assets.exclusion_reason("BTCUP", known) == "leveraged token"
    assert assets.exclusion_reason("SUSHIDOWN", known) == "leveraged token"
    assert assets.exclusion_reason("ADABULL", known) == "leveraged token"
    assert assets.exclusion_reason("1INCHUP", known) == "leveraged token"
    assert assets.exclusion_reason("JUP", known) is None  # Jupiter, not J + UP
    assert assets.exclusion_reason("BTC", known) is None
    assert assets.exclusion_reason("USDC", known) == "stablecoin"
    assert assets.exclusion_reason("EUR", known) == "fiat currency"
    assert assets.exclusion_reason("WBTC", known) == "wrapped or pegged asset"
    assert assets.exclusion_reason("PAXG", known) == "wrapped or pegged asset"
    assert assets.leveraged_base("UP", known) is None  # the suffix alone is not a token


# --- restrictions --------------------------------------------------------------------------------


def test_packaged_restrictions_load_and_expand_to_pairs(conn):
    result = restrictions.seed(conn)
    assert result.restrictions == 23 and result.placeholders == 23 and result.pairs == 23  # no listed pair in this fixture
    rows = conn.execute("SELECT symbol, valid_from, valid_to FROM restrictions WHERE jurisdiction = 'BR' ORDER BY symbol").fetchall()
    assert len(rows) == 23 and all(r["valid_from"] == "2026-10-27" and r["valid_to"] is None for r in rows)
    assert {"XVGUSDT", "OMUSDT", "ICXUSDT", "HFTUSDT"} <= {r["symbol"] for r in rows}
    again = restrictions.seed(conn)
    assert again.pairs == 23 and conn.execute("SELECT COUNT(*) FROM restrictions").fetchone()[0] == 23


def test_restrictions_csv_is_validated(tmp_path: Path, conn):
    bad = tmp_path / "bad.csv"
    bad.write_text("jurisdiction,asset\nBR,XVG\n")
    with pytest.raises(restrictions.RestrictionsError, match="expected columns"):
        restrictions.seed(conn, [bad])
    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text("jurisdiction,asset,valid_from,valid_to,reason,source\nBR,,2026-01-01,,x,y\n")
    with pytest.raises(restrictions.RestrictionsError, match="line 2"):
        restrictions.seed(conn, [incomplete])


def br_csv(tmp_path: Path) -> Path:
    path = tmp_path / "br.csv"
    path.write_text("jurisdiction,asset,valid_from,valid_to,reason,source\nbr,jup,2024-05-01,,test restriction,unit test\n")
    return path


# --- build ---------------------------------------------------------------------------------------


def test_build_applies_every_rule_in_order(conn):
    result = build.build(conn)
    assert (result.first_date, result.last_date) == ("2024-01-02", "2024-07-01")
    assert result.dates == 182
    assert result.jurisdiction == ""

    # History: 60 traded days are needed; BTC has them on Feb 29, so March 1 is the first eligible decision.
    assert universe_row(conn, "BTCUSDT", N[59])["exclusion_reason"] == "history shorter than 60 traded days"
    btc = universe_row(conn, "BTCUSDT", N[60])
    assert (btc["eligible"], btc["rank"], btc["history_days"], btc["median_volume_30"]) == (1, 1, 60, 100e6)
    assert universe_row(conn, "ETHUSDT", N[60])["rank"] == 2
    assert universe_row(conn, "JUPUSDT", N[60])["rank"] == 5  # JUP is Jupiter, not a leveraged token; GAP and ZERO (30M) rank above it
    assert universe_row(conn, "SMALLUSDT", N[60])["exclusion_reason"] == "median volume below minimum"
    assert universe_row(conn, "BTCUPUSDT", N[60])["exclusion_reason"] == "leveraged token"
    assert universe_row(conn, "USDCUSDT", N[60])["exclusion_reason"] == "stablecoin"
    assert universe_row(conn, "BTCBUSD", N[60])["exclusion_reason"] == "quote asset is not USDT"
    assert universe_row(conn, "USDTBRL", N[60])["exclusion_reason"] == "quote asset is not USDT"  # before 'stablecoin'

    # A quality finding on D-1 excludes D; the rank passes to the next pair that day.
    assert universe_row(conn, "BTCUSDT", N[106])["exclusion_reason"] == "quality: ohlc"  # April 16, the finding is on April 15
    assert universe_row(conn, "ETHUSDT", N[106])["rank"] == 1
    assert universe_row(conn, "BTCUSDT", N[107])["rank"] == 1
    # Gap findings and findings on other intervals are not applied.
    assert universe_row(conn, "ETHUSDT", N[MAY1 + 1])["eligible"] == 1

    # A new listing waits 60 traded days: May 1 to June 29, eligible on June 30.
    assert universe_row(conn, "NEWUSDT", N[180])["exclusion_reason"] == "history shorter than 60 traded days"
    assert universe_row(conn, "NEWUSDT", N[181])["eligible"] == 1

    # A gap longer than 7 days restarts the clock: GAP was eligible in March, is absent during the gap,
    # and is treated as a new listing from April 21 (60 traded days later: June 20).
    assert universe_row(conn, "GAPUSDT", N[75])["eligible"] == 1
    assert universe_row(conn, "GAPUSDT", N[APR1 + 1]) is None  # no candle on April 1, no candidate on April 2
    after = universe_row(conn, "GAPUSDT", N[APR20 + 2])  # April 22, built from the April 21 candle
    assert (after["eligible"], after["history_days"], after["exclusion_reason"]) == (0, 1, "history shorter than 60 traded days")
    assert universe_row(conn, "GAPUSDT", N[APR20 + 60])["exclusion_reason"] == "history shorter than 60 traded days"  # June 19: 59 days
    assert universe_row(conn, "GAPUSDT", N[APR20 + 61])["eligible"] == 1  # June 20: 60 days

    # Zero-volume days are not traded days: no candidate, but no restart either (4 days is a short gap).
    assert universe_row(conn, "ZEROUSDT", N[69]) is None and universe_row(conn, "ZEROUSDT", N[72]) is None
    resumed = universe_row(conn, "ZEROUSDT", N[73])  # March 14, from the March 13 candle
    assert resumed["eligible"] == 1 and resumed["history_days"] == 69

    assert result.last_eligible == 6  # BTC, ETH, JUP, NEW, GAP, ZERO on July 1
    assert result.last_top[0] == ("BTCUSDT", 100e6) and result.last_top[1] == ("ETHUSDT", 50e6)
    assert result.last_exclusions == {"leveraged token": 1, "median volume below minimum": 1, "stablecoin": 1, "quote asset is not USDT": 2}


def test_build_with_jurisdiction_keeps_both_universes(conn, tmp_path: Path):
    restrictions.seed(conn, [br_csv(tmp_path)])
    build.build(conn)
    br = build.build(conn, jurisdiction="BR")
    assert br.jurisdiction == "BR"
    assert universe_row(conn, "JUPUSDT", N[120], "BR")["eligible"] == 1  # April 30
    assert universe_row(conn, "JUPUSDT", N[MAY1 + 1], "BR")["exclusion_reason"] == "restricted in BR"  # May 2
    assert universe_row(conn, "JUPUSDT", N[MAY1 + 1], "")["eligible"] == 1  # the global universe is untouched
    assert conn.execute("SELECT COUNT(DISTINCT jurisdiction) FROM universe").fetchone()[0] == 2
    assert br.last_exclusions["restricted in BR"] == 1


def test_build_date_range_replaces_only_that_range(conn):
    build.build(conn)
    total = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
    partial = build.build(conn, start=date(2024, 3, 1), end=date(2024, 3, 31))
    assert (partial.first_date, partial.last_date, partial.dates) == ("2024-03-01", "2024-03-31", 31)
    assert conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0] == total


def test_build_parameters_change_the_verdicts(conn):
    build.build(conn, min_volume=1_000.0, min_history=10)
    assert universe_row(conn, "SMALLUSDT", N[60])["eligible"] == 1
    assert universe_row(conn, "BTCUSDT", N[10])["eligible"] == 1


def test_build_without_parquet_fails(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "empty"))
    config.ensure_dirs()
    connection, _ = store.init()
    with pytest.raises(build.UniverseError, match="no daily Parquet partitions"):
        build.build(connection)
    connection.close()


def test_show_lists_ranked_pairs_and_exclusions(conn):
    build.build(conn)
    rows, excluded = build.show(conn, date(2024, 3, 1), top=2)
    assert [(r["symbol"], r["rank"]) for r in rows] == [("BTCUSDT", 1), ("ETHUSDT", 2)]
    assert excluded == {"median volume below minimum": 1, "leveraged token": 1, "stablecoin": 1, "quote asset is not USDT": 2}
    assert build.latest_date(conn) == date(2024, 7, 1)
    assert build.latest_date(conn, "BR") is None


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_universe(conn, tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["universe", "restrictions", "--csv", str(br_csv(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "ok      1 restrictions read, 1 pair rows written, 0 assets with no listed pair yet" in result.output

    result = runner.invoke(app, ["universe", "build", "-j", "br"])
    assert result.exit_code == 0, result.output
    assert "  last decision date 2024-07-01: 5 eligible pairs" in result.output
    assert "    BTCUSDT      median 30d quote volume 100.0M" in result.output
    assert "    excluded, restricted in BR: 1" in result.output
    assert "decision dates (2024-01-02 to 2024-07-01) for jurisdiction 'BR'" in result.output

    result = runner.invoke(app, ["universe", "show", "-j", "BR", "--date", "2024-06-01", "--top", "3"])
    assert result.exit_code == 0, result.output
    assert "decision date 2024-06-01, jurisdiction 'BR': top 3 eligible pairs" in result.output
    assert "    1  BTCUSDT      median 30d quote volume      100.0M  history 152 days" in result.output
    assert "  excluded, restricted in BR: 1" in result.output
    assert "ok      3 shown, 8 excluded on 2024-06-01" in result.output

    result = runner.invoke(app, ["universe", "show"])  # nothing built for the default jurisdiction
    assert result.exit_code == 1
    assert "why: nothing built for jurisdiction '-'" in result.output

    result = runner.invoke(app, ["universe", "build", "--from", "soon"])
    assert result.exit_code == 1 and "why: --from 'soon' is not a date" in result.output
