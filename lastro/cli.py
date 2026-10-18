"""The ``lastro`` command.

``doctor`` and the ``db`` group check and prepare the environment. The ``data``
group is the pipeline in order: ``list``, ``fetch``, ``verify``, ``convert``,
``quality``, ``freeze``. Commands that have not landed yet say so.

Every command ends with one result line (``ok      ...`` or ``FAILED  ...``),
and every failure says what broke, why, and what to do. See ``diagnostics``.
"""

from __future__ import annotations

import json
import signal
import threading
import time
from datetime import datetime, timezone

import typer

from lastro import __version__, config, diagnostics
from lastro.db import store


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open_db():
    """Open the database for a command, failing with instructions if it is missing or behind the code."""
    path = config.db_path()
    if not path.exists():
        diagnostics.fail("opening the database", f"no database at {path}", "run `lastro db init`")
    conn = store.connect(path)
    pending = store.pending_versions(conn)
    if pending:
        conn.close()
        diagnostics.fail(
            "opening the database",
            f"schema is behind the code: {', '.join(pending)} not applied",
            "run `lastro db init` once; existing data is kept",
        )
    return conn


def _record_run(conn, command: str, parameters: dict, started_at: str, outcome: str, detail: str) -> None:
    with store.transaction(conn):
        conn.execute(
            "INSERT INTO runs (command, parameters, started_at, finished_at, outcome, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (command, json.dumps(parameters, sort_keys=True), started_at, _utcnow(), outcome, detail),
        )

app = typer.Typer(
    help="Lastro: long/cash spot crypto allocator with a verifiable research laboratory.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Operational database (SQLite).", no_args_is_help=True)
data_app = typer.Typer(help="Market data pipeline.", no_args_is_help=True)
collect_app = typer.Typer(help="Live samplers (order book).", no_args_is_help=True)
universe_app = typer.Typer(help="Point-in-time universe.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(data_app, name="data")
app.add_typer(collect_app, name="collect")
app.add_typer(universe_app, name="universe")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lastro {__version__}")
        raise typer.Exit()


def _terminate(signum, frame) -> None:
    """A `kill` (SIGTERM) behaves like Ctrl-C: long commands stop cleanly and say how to resume."""
    raise KeyboardInterrupt


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", help="Show the version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Root command. Subcommands do the work."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _terminate)


@app.command("doctor")
def doctor(
    offline: bool = typer.Option(False, "--offline", help="Skip the network check."),
) -> None:
    """Check the environment and say exactly what passes, what fails, and how to fix it."""
    checks = diagnostics.ALL_CHECKS
    if offline:
        checks = tuple(check for check in checks if check is not diagnostics.check_network)
    typer.echo(f"lastro {__version__} doctor")
    text, code = diagnostics.render(diagnostics.run_checks(checks))
    typer.echo(text)
    raise typer.Exit(code=code)


@db_app.command("init")
def db_init() -> None:
    """Create the data directories and the database, and apply pending migrations."""
    try:
        root = config.ensure_dirs()
    except OSError as exc:
        diagnostics.fail(
            "creating the data directory",
            f"{config.data_dir()} is not writable ({exc.strerror or exc})",
            f"fix the permissions, or point {config.ENV_DATA_DIR} at a directory you can write to",
        )
    try:
        conn, applied = store.init()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        diagnostics.fail(
            "initializing the database",
            f"{config.db_path()}: {exc}",
            "if the file is corrupt, move it aside and run `lastro db init` again (Phase 1 data is rebuildable)",
        )
    typer.echo(f"data dir: {root}")
    typer.echo(f"database: {config.db_path()}")
    if applied:
        diagnostics.done("database initialized, applied " + ", ".join(applied))
    else:
        diagnostics.done("database already current, nothing to apply")


@db_app.command("status")
def db_status() -> None:
    """Show schema version and row counts per table."""
    path = config.db_path()
    if not path.exists():
        diagnostics.fail("reading the database", f"no database at {path}", "run `lastro db init`")
    conn = store.connect(path)
    try:
        versions = store.applied_versions(conn)
        counts = store.table_counts(conn)
    finally:
        conn.close()
    typer.echo(f"database: {path}")
    typer.echo(f"schema version: {versions[-1] if versions else 'none'}")
    width = max(len(name) for name in counts) if counts else 0
    for name, count in counts.items():
        typer.echo(f"  {name.ljust(width)}  {count}")
    diagnostics.done(f"{len(counts)} tables, schema version {versions[-1] if versions else 'none'}")


@data_app.command("list")
def data_list(
    symbol: list[str] = typer.Option(
        None, "--symbol", "-s", help="Limit to these pairs (repeatable). Default: every USDT pair."
    ),
    interval: list[str] = typer.Option(
        None, "--interval", "-i", help="Limit to these intervals (repeatable). Default: 1d, 4h, 1h."
    ),
) -> None:
    """Enumerate the public archive: which pairs and months exist, alive or dead. Downloads nothing."""
    from lastro.data import archive

    intervals = tuple(interval) if interval else archive.INTERVALS
    symbols = [s.upper() for s in symbol] if symbol else None
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        try:
            result = archive.enumerate_archive(
                conn, intervals=intervals, symbols=symbols, progress=lambda msg: typer.echo(f"  {msg}")
            )
        except archive.ArchiveError as exc:
            _record_run(conn, "data list", {"symbols": symbols, "intervals": list(intervals)}, started_at, "failed", str(exc))
            diagnostics.fail("listing the archive", str(exc), "what was listed before the failure is kept; run again to continue")
        elapsed = time.perf_counter() - clock
        summary = f"{result.pairs} pairs, {result.files} files listed in {elapsed:.0f} s"
        if result.empty_pairs:
            summary += f"; {len(result.empty_pairs)} pairs with no files in these intervals: " + ", ".join(result.empty_pairs)
        if result.failed_pairs:
            for failed_symbol, why in result.failed_pairs:
                typer.echo(f"  FAIL  {failed_symbol}: {why}", err=True)
            _record_run(conn, "data list", {"symbols": symbols, "intervals": list(intervals)}, started_at, "partial", summary)
            diagnostics.fail(
                "listing the archive",
                f"{len(result.failed_pairs)} pairs failed; {summary}",
                "run `lastro data list` again: pairs already listed are kept, failed ones are retried",
            )
        _record_run(conn, "data list", {"symbols": symbols, "intervals": list(intervals)}, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


_SYMBOL_OPTION = typer.Option(None, "--symbol", "-s", help="Limit to these pairs (repeatable).")
_INTERVAL_OPTION = typer.Option(None, "--interval", "-i", help="Limit to these intervals (repeatable).")


def _filters(symbol: list[str] | None, interval: list[str] | None) -> tuple[list[str] | None, tuple[str, ...] | None]:
    symbols = [s.upper() for s in symbol] if symbol else None
    intervals = tuple(interval) if interval else None
    return symbols, intervals


@data_app.command("fetch")
def data_fetch(
    symbol: list[str] = _SYMBOL_OPTION,
    interval: list[str] = _INTERVAL_OPTION,
    workers: int = typer.Option(8, "--workers", min=1, max=32, help="Parallel downloads."),
) -> None:
    """Download the pending files `data list` found, 1d first, with their .CHECKSUM. Resumable."""
    from lastro.data import fetch

    symbols, intervals = _filters(symbol, interval)
    params = {"symbols": symbols, "intervals": list(intervals) if intervals else None, "workers": workers}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        try:
            result = fetch.fetch_pending(
                conn, symbols=symbols, intervals=intervals, progress=lambda m: typer.echo(f"  {m}"), workers=workers
            )
        except KeyboardInterrupt:
            _record_run(conn, "data fetch", params, started_at, "interrupted", "stopped by the operator")
            diagnostics.fail("downloading the archive", "interrupted", "run `lastro data fetch` again: it resumes where it stopped")
        elapsed = time.perf_counter() - clock
        total = result.downloaded + result.skipped
        summary = (
            f"{result.downloaded} downloaded, {result.skipped} already on disk, "
            f"{result.checksums}/{total} .CHECKSUM files, {result.bytes / 1e6:.1f} MB, {fetch.duration(elapsed)}"
        )
        if result.failed:
            for key, why in result.failed:
                typer.echo(f"  FAIL  {key}: {why}", err=True)
            _record_run(conn, "data fetch", params, started_at, "partial", summary)
            diagnostics.fail(
                "downloading the archive",
                f"{len(result.failed)} files failed; {summary}",
                "run `lastro data fetch` again: files already on disk are kept, failed ones are retried",
            )
        _record_run(conn, "data fetch", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@data_app.command("verify")
def data_verify(symbol: list[str] = _SYMBOL_OPTION, interval: list[str] = _INTERVAL_OPTION) -> None:
    """Check downloaded files against the SHA-256 the archive publishes (read from disk when fetch stored it)."""
    from lastro.data import checksum, fetch

    symbols, intervals = _filters(symbol, interval)
    params = {"symbols": symbols, "intervals": list(intervals) if intervals else None}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        try:
            result = checksum.verify_downloaded(conn, symbols=symbols, intervals=intervals, progress=lambda m: typer.echo(f"  {m}"))
        except KeyboardInterrupt:
            _record_run(conn, "data verify", params, started_at, "interrupted", "stopped by the operator")
            diagnostics.fail("verifying the archive", "interrupted", "run `lastro data verify` again: files already verified are kept")
        elapsed = time.perf_counter() - clock
        summary = (
            f"{result.verified} verified, {len(result.failed)} failed "
            f"(checksums: {result.from_disk} from disk, {result.fetched} fetched"
            + (f", {result.from_manifest} from the manifest" if result.from_manifest else "") + f"), {fetch.duration(elapsed)}"
        )
        if result.failed:
            for key, why in result.failed:
                typer.echo(f"  FAIL  {key}: {why}", err=True)
            _record_run(conn, "data verify", params, started_at, "partial", summary)
            diagnostics.fail(
                "verifying the archive",
                f"{len(result.failed)} files failed; {summary}",
                "mismatched files were deleted and re-queued: run `lastro data fetch` then `lastro data verify` again",
            )
        _record_run(conn, "data verify", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@data_app.command("convert")
def data_convert(symbol: list[str] = _SYMBOL_OPTION, interval: list[str] = _INTERVAL_OPTION) -> None:
    """Convert verified zips into partitioned Parquet (one file per pair, interval and year)."""
    from lastro.data import parquet

    symbols, intervals = _filters(symbol, interval)
    params = {"symbols": symbols, "intervals": list(intervals) if intervals else None}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        try:
            result = parquet.convert_verified(conn, symbols=symbols, intervals=intervals, progress=lambda m: typer.echo(f"  {m}"))
        except KeyboardInterrupt:
            _record_run(conn, "data convert", params, started_at, "interrupted", "stopped by the operator")
            diagnostics.fail("converting the archive", "interrupted", "run `lastro data convert` again: partitions are rebuilt whole, nothing is left half-written")
        elapsed = time.perf_counter() - clock
        summary = f"{result.files} files, {result.rows} rows into {result.partitions} partitions"
        if result.duplicates:
            summary += f", {result.duplicates} boundary duplicates dropped"
        summary += f", {elapsed:.0f} s"
        if result.failed:
            for key, why in result.failed:
                typer.echo(f"  FAIL  {key}: {why}", err=True)
            _record_run(conn, "data convert", params, started_at, "partial", summary)
            diagnostics.fail(
                "converting the archive",
                f"{len(result.failed)} files failed; {summary}",
                "partitions containing a failed file were not written; fix the cause (see each FAIL line) and run again",
            )
        _record_run(conn, "data convert", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


def _short(stamp: str) -> str:
    return stamp.replace("T", " ").replace("+00:00", "")


def _issue_line(issue) -> str:
    period = _short(issue.period_start)
    if issue.period_end and issue.period_end != issue.period_start:
        period += " .. " + _short(issue.period_end)
    where = f"{issue.symbol} {issue.interval}" + (f" {period}" if period else "")
    return f"{where}: {issue.detail} -> {issue.action}"


@data_app.command("quality")
def data_quality(
    symbol: list[str] = _SYMBOL_OPTION,
    interval: list[str] = _INTERVAL_OPTION,
    show_all: bool = typer.Option(False, "--all", help="Print every finding, not just a few examples per type."),
) -> None:
    """Run the quality checks over the Parquet and record every finding in quality_issues. Corrects nothing."""
    from lastro.data import quality

    symbols, intervals = _filters(symbol, interval)
    params = {"symbols": symbols, "intervals": list(intervals) if intervals else None}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        try:
            result = quality.run_checks(conn, intervals=intervals, symbols=symbols, progress=lambda m: typer.echo(f"  {m}"))
        except quality.QualityError as exc:
            _record_run(conn, "data quality", params, started_at, "failed", str(exc))
            diagnostics.fail("running the quality checks", str(exc), "check the options and run again")
        except KeyboardInterrupt:
            _record_run(conn, "data quality", params, started_at, "interrupted", "stopped by the operator")
            diagnostics.fail("running the quality checks", "interrupted", "run `lastro data quality` again; nothing was recorded")
        if not result.intervals_checked:
            _record_run(conn, "data quality", params, started_at, "failed", "no Parquet partitions")
            scope = "for " + ", ".join(symbols) if symbols else "at all"
            diagnostics.fail(
                "running the quality checks",
                f"no Parquet partitions found {scope}",
                "run `lastro data fetch`, `lastro data verify` and `lastro data convert` first",
            )
        recorded = quality.record(conn, result, symbols)
        elapsed = time.perf_counter() - clock
        hard = quality.QualityResult.counts(result.hard_failures)
        soft = quality.QualityResult.counts(result.issues)
        for check, count in soft.items():
            typer.echo(f"  {check.ljust(15)} {count}")
            examples = [i for i in result.issues if i.check == check]
            for issue in examples if show_all else examples[:5]:
                typer.echo(f"    {_issue_line(issue)}")
            if not show_all and len(examples) > 5:
                typer.echo(f"    ... {len(examples) - 5} more in quality_issues (use --all to print them)")
        for issue in result.hard_failures:
            typer.echo(f"  FAIL  {_issue_line(issue)}", err=True)
        summary = (
            f"{result.rows_checked} rows checked, {recorded} findings recorded in quality_issues"
            + (" (" + ", ".join(f"{k} {v}" for k, v in soft.items()) + ")" if soft else "")
            + f", {elapsed:.0f} s"
        )
        if result.missing:
            summary += "; no Parquet for " + ", ".join(result.missing)
        if result.hard_failures:
            _record_run(conn, "data quality", params, started_at, "failed", summary)
            fixes = {
                "source": "source: rows came from zips that are not verified; run `lastro data verify` "
                          "then `lastro data convert -s <pair>` to rebuild them",
                "timestamp": "timestamp: candles before 2017 or in the future mean the converter misread "
                             "the unit; this is a Lastro bug, open an issue with the FAIL lines",
            }
            diagnostics.fail(
                "quality checks",
                f"{len(result.hard_failures)} hard failures ({', '.join(f'{k} {v}' for k, v in hard.items())}); "
                "the Parquet cannot be used as it stands",
                "; ".join(fixes[k] for k in hard),
            )
        _record_run(conn, "data quality", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@data_app.command("status")
def data_status() -> None:
    """Where the pipeline stands: files per interval and status, disk usage, last run of each command."""
    from lastro.data import status as status_module

    conn = _open_db()
    try:
        status = status_module.collect(conn)
    finally:
        conn.close()
    typer.echo(f"database: {config.db_path()}")
    typer.echo(f"data dir: {config.data_dir()}")
    for line in status_module.render(status):
        typer.echo(line)
    diagnostics.done(status_module.summary(status))


@data_app.command("exchange-info")
def data_exchange_info() -> None:
    """Capture today's exchangeInfo: symbol status and trading rules, and refresh symbols.alive_today."""
    from lastro.data import exchange_info

    conn = _open_db()
    started_at = _utcnow()
    params = {"api": config.binance_api()}
    try:
        try:
            result = exchange_info.snapshot(conn)
        except exchange_info.ExchangeInfoError as exc:
            _record_run(conn, "data exchange-info", params, started_at, "failed", str(exc))
            diagnostics.fail(
                "capturing exchangeInfo", str(exc),
                "check the connection; the endpoint is public and needs no key. If this host is blocked for you, "
                f"set {config.ENV_BINANCE_API} (https://api.binance.com, or a regional mirror)",
            )
        summary = (
            f"{result.symbols} symbols in the snapshot, {result.trading} trading ({result.usdt_trading} USDT pairs); "
            f"{result.alive} archive pairs alive today; saved {result.path}"
        )
        _record_run(conn, "data exchange-info", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@data_app.command("fx")
def data_fx(
    since: str = typer.Option(None, "--since", help="Refetch PTAX from this date (YYYY-MM-DD). Default: continue from the last stored day."),
) -> None:
    """Update currency rates: PTAX (sell and buy) from the Central Bank, USDTBRL closes from the Parquet."""
    from datetime import date

    from lastro.data import fx

    start = None
    if since:
        try:
            start = date.fromisoformat(since)
        except ValueError:
            diagnostics.fail("reading the options", f"--since {since!r} is not a date", "use YYYY-MM-DD")
    conn = _open_db()
    started_at = _utcnow()
    params = {"since": since}
    try:
        try:
            result = fx.update_ptax(conn, start=start, progress=lambda m: typer.echo(f"  {m}"))
        except fx.FxError as exc:
            _record_run(conn, "data fx", params, started_at, "failed", str(exc))
            diagnostics.fail("fetching PTAX", str(exc), "the Central Bank API (api.bcb.gov.br) is public; check the connection and try again")
        result.usdtbrl_rows = fx.update_usdtbrl(conn)
        summary = f"{result.ptax_rows} PTAX days added"
        if result.first:
            summary += f", series now {result.first} to {result.last}"
        summary += f"; {result.usdtbrl_rows} USDTBRL closes"
        if result.usdtbrl_rows == 0:
            summary += " (none converted yet: run `lastro data list -s USDTBRL -i 1d`, then fetch, verify, convert)"
        _record_run(conn, "data fx", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


def _date_option(value: str | None, name: str):
    from datetime import date

    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        diagnostics.fail("reading the options", f"{name} {value!r} is not a date", "use YYYY-MM-DD")


@data_app.command("freeze")
def data_freeze(
    cutoff: str = typer.Option(..., "--cutoff", help="Last complete month to include (YYYY-MM)."),
    verify: bool = typer.Option(False, "--verify", help="Compare against an existing manifest instead of writing one."),
    manifest_file: str = typer.Option(None, "--manifest", help="Manifest to compare with (default: data/manifest.json)."),
) -> None:
    """Hash every archive file and partition up to the cutoff into data/manifest.json: the dataset's identity."""
    from pathlib import Path

    from lastro.data import freeze

    conn = _open_db()
    started_at = _utcnow()
    params = {"cutoff": cutoff, "verify": verify, "manifest": manifest_file}
    clock = time.perf_counter()
    try:
        try:
            manifest = freeze.build_manifest(conn, cutoff, progress=lambda m: typer.echo(f"  {m}"))
        except freeze.FreezeError as exc:
            _record_run(conn, "data freeze", params, started_at, "failed", str(exc))
            diagnostics.fail("freezing the dataset", str(exc), "fix the cause above and run again")
        elapsed = time.perf_counter() - clock
        rows = sum(manifest["rows_by_interval"].values())
        if verify:
            path = Path(manifest_file) if manifest_file else config.manifest_path()
            try:
                expected = freeze.read(path)
            except freeze.FreezeError as exc:
                _record_run(conn, "data freeze", params, started_at, "failed", str(exc))
                diagnostics.fail("verifying the dataset", str(exc), "point --manifest at the manifest you want to reproduce")
            differences = freeze.compare(expected, manifest)
            summary = (
                f"{len(manifest['files'])} files, {len(manifest['partitions'])} partitions, {rows} rows up to {cutoff}; "
                f"dataset hash {manifest['dataset_hash'][:16]}..., {elapsed:.0f} s"
            )
            if differences or expected["dataset_hash"] != manifest["dataset_hash"]:
                for line in differences[:20]:
                    typer.echo(f"  DIFF  {line}", err=True)
                if len(differences) > 20:
                    typer.echo(f"  ... {len(differences) - 20} more differences", err=True)
                _record_run(conn, "data freeze", params, started_at, "failed", f"does not match {path}: {len(differences)} differences")
                diagnostics.fail(
                    "verifying the dataset",
                    f"this data does not reproduce {path} ({len(differences)} differences; expected hash {expected['dataset_hash'][:16]}..., got {manifest['dataset_hash'][:16]}...)",
                    "see the DIFF lines; a file difference means a different archive version, a partition difference means a conversion difference",
                )
            summary = f"reproduces {path}: " + summary
            _record_run(conn, "data freeze", params, started_at, "ok", summary)
        else:
            path = freeze.write(manifest)
            freeze.record(conn, manifest)
            summary = (
                f"{len(manifest['files'])} files, {len(manifest['partitions'])} partitions, {rows} rows up to {cutoff}; "
                f"dataset hash {manifest['dataset_hash']}; written {path}, {elapsed:.0f} s"
            )
            _record_run(conn, "data freeze", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@data_app.command("restore")
def data_restore(
    manifest_file: str = typer.Argument(..., help="Manifest to restore the archive listing from."),
) -> None:
    """Register a manifest's files and pairs in the database, so a copy of raw/ can be verified and converted."""
    from pathlib import Path

    from lastro.data import freeze

    conn = _open_db()
    started_at = _utcnow()
    params = {"manifest": manifest_file}
    try:
        try:
            manifest = freeze.read(Path(manifest_file))
            result = freeze.restore(conn, manifest)
        except freeze.FreezeError as exc:
            _record_run(conn, "data restore", params, started_at, "failed", str(exc))
            diagnostics.fail("restoring from the manifest", str(exc), "use a manifest written by `lastro data freeze`")
        summary = (
            f"{result.files} files registered ({result.on_disk} already in raw/, {result.files - result.on_disk} to fetch), "
            f"{result.symbols} pairs; next: `lastro data verify`, `lastro data convert`, then `lastro data freeze --cutoff {manifest['cutoff_month']} --verify --manifest {manifest_file}`"
        )
        _record_run(conn, "data restore", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@collect_app.command("orderbook")
def collect_orderbook(
    symbol: list[str] = typer.Option(..., "--symbol", "-s", help="Pairs to sample (repeatable)."),
    minutes: float = typer.Option(0, "--minutes", min=0, help="How long to run. 0 = until Ctrl-C."),
    every: float = typer.Option(60, "--every", min=1, help="Seconds between rounds."),
    depth_limit: int = typer.Option(1000, "--depth-limit", help="Price levels per side to request (100, 500, 1000 or 5000)."),
) -> None:
    """Sample the order book of the given pairs and store the depth profile the cost model needs."""
    from lastro.data import orderbook

    symbols = [s.upper() for s in symbol]
    rounds = max(1, int(minutes * 60 // every)) if minutes else None
    params = {"symbols": symbols, "minutes": minutes, "every": every, "depth_limit": depth_limit, "api": config.binance_api()}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        plan = f"{len(symbols)} pairs every {every:g} s, " + (f"{rounds} rounds" if rounds else "until Ctrl-C")
        typer.echo(f"  {plan}; writing to {orderbook.orderbook_dir()}")
        result = orderbook.collect(
            symbols, rounds=rounds, every=every, depth_limit=depth_limit, progress=lambda m: typer.echo(f"  {m}")
        )
        elapsed = time.perf_counter() - clock
        for name, s in result.summary.items():
            typer.echo(
                f"  {name}: {s.snapshots} snapshots, mid {s.mid:g}, spread {s.spread_bps:.3f} bps; quote within 10 bps of mid: "
                f"{orderbook.format_quote(s.bid_depth_10bps)} bid / {orderbook.format_quote(s.ask_depth_10bps)} ask; within 50 bps: "
                f"{orderbook.format_quote(s.bid_depth_50bps)} / {orderbook.format_quote(s.ask_depth_50bps)}; book reaches {s.covered_bps:.1f} bps"
            )
        summary = f"{result.snapshots} snapshots of {len(symbols)} pairs in {result.rounds} rounds, {result.files} files, {elapsed:.0f} s"
        if result.failed:
            for name, why in result.failed[:10]:
                typer.echo(f"  FAIL  {name}: {why}", err=True)
            if len(result.failed) > 10:
                typer.echo(f"  ... {len(result.failed) - 10} more failed requests", err=True)
            _record_run(conn, "collect orderbook", params, started_at, "partial", summary)
            diagnostics.fail(
                "sampling the order book",
                f"{len(result.failed)} requests failed; {summary}",
                "snapshots that succeeded were written; see the FAIL lines for the cause",
            )
        if result.snapshots == 0:
            _record_run(conn, "collect orderbook", params, started_at, "failed", summary)
            diagnostics.fail("sampling the order book", "no snapshot was taken", "check the pair names and the connection")
        _record_run(conn, "collect orderbook", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@universe_app.command("restrictions")
def universe_restrictions(
    csv_file: list[str] = typer.Option(None, "--csv", help="Restriction files to load (default: the ones shipped with Lastro)."),
) -> None:
    """Load dated restriction lists (asset, jurisdiction, dates) into the restrictions table."""
    from pathlib import Path

    from lastro.universe import restrictions

    conn = _open_db()
    started_at = _utcnow()
    params = {"csv": csv_file}
    try:
        try:
            result = restrictions.seed(conn, [Path(p) for p in csv_file] if csv_file else None)
        except restrictions.RestrictionsError as exc:
            _record_run(conn, "universe restrictions", params, started_at, "failed", str(exc))
            diagnostics.fail("loading restrictions", str(exc), "fix the CSV (columns: jurisdiction, asset, valid_from, valid_to, reason, source)")
        summary = f"{result.restrictions} restrictions read, {result.pairs} pair rows written, {result.placeholders} assets with no listed pair yet"
        _record_run(conn, "universe restrictions", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


_JURISDICTION_OPTION = typer.Option("", "--jurisdiction", "-j", help="Apply this jurisdiction's restrictions (e.g. BR). Default: none.")


@universe_app.command("build")
def universe_build(
    jurisdiction: str = _JURISDICTION_OPTION,
    min_volume: float = typer.Option(500_000.0, "--min-volume", help="Minimum 30-day median quote volume."),
    min_history: int = typer.Option(60, "--min-history", help="Minimum traded days since the last long gap."),
    long_gap: int = typer.Option(7, "--long-gap", help="A gap longer than this many days restarts a pair's history."),
    since: str = typer.Option(None, "--from", help="First decision date (YYYY-MM-DD)."),
    until: str = typer.Option(None, "--to", help="Last decision date (YYYY-MM-DD)."),
    symbol: list[str] = _SYMBOL_OPTION,
) -> None:
    """Compute point-in-time eligibility and liquidity rank for every pair on every decision date."""
    from lastro.universe import build as universe

    start, end = _date_option(since, "--from"), _date_option(until, "--to")
    symbols = [s.upper() for s in symbol] if symbol else None
    jurisdiction = jurisdiction.upper()
    params = {"jurisdiction": jurisdiction, "min_volume": min_volume, "min_history": min_history, "long_gap": long_gap, "from": since, "to": until, "symbols": symbols}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        try:
            result = universe.build(
                conn, jurisdiction=jurisdiction, min_volume=min_volume, min_history=min_history, long_gap_days=long_gap,
                start=start, end=end, symbols=symbols, progress=lambda m: typer.echo(f"  {m}"),
            )
        except universe.UniverseError as exc:
            _record_run(conn, "universe build", params, started_at, "failed", str(exc))
            diagnostics.fail("building the universe", str(exc), "run the data pipeline first (list, fetch, verify, convert, quality)")
        elapsed = time.perf_counter() - clock
        if result.last_date:
            typer.echo(f"  last decision date {result.last_date}: {result.last_eligible} eligible pairs")
            for symbol_name, volume in result.last_top:
                typer.echo(f"    {symbol_name.ljust(12)} median 30d quote volume {volume / 1e6:,.1f}M")
            for reason, count in sorted(result.last_exclusions.items(), key=lambda kv: -kv[1]):
                typer.echo(f"    excluded, {reason}: {count}")
        summary = (
            f"{result.rows} rows over {result.dates} decision dates ({result.first_date} to {result.last_date}) "
            f"for jurisdiction '{jurisdiction or '-'}', {elapsed:.0f} s"
        )
        _record_run(conn, "universe build", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@universe_app.command("show")
def universe_show(
    on: str = typer.Option(None, "--date", help="Decision date (YYYY-MM-DD). Default: the latest built."),
    jurisdiction: str = _JURISDICTION_OPTION,
    top: int = typer.Option(50, "--top", help="How many eligible pairs to list."),
) -> None:
    """Print the ranking stored for one decision date and why the rest were out."""
    from lastro.universe import build as universe

    jurisdiction = jurisdiction.upper()
    conn = _open_db()
    try:
        day = _date_option(on, "--date") or universe.latest_date(conn, jurisdiction)
        if day is None:
            diagnostics.fail("showing the universe", f"nothing built for jurisdiction '{jurisdiction or '-'}'", "run `lastro universe build` first")
        rows, excluded = universe.show(conn, day, jurisdiction, top)
    finally:
        conn.close()
    if not rows and not excluded:
        diagnostics.fail("showing the universe", f"no rows for {day.isoformat()} and jurisdiction '{jurisdiction or '-'}'", "check the date, or run `lastro universe build` for that range")
    typer.echo(f"decision date {day.isoformat()}, jurisdiction '{jurisdiction or '-'}': top {len(rows)} eligible pairs")
    for r in rows:
        typer.echo(f"  {str(r['rank']).rjust(3)}  {r['symbol'].ljust(12)} median 30d quote volume {r['median_volume_30'] / 1e6:>10,.1f}M  history {r['history_days']} days")
    for reason, count in excluded.items():
        typer.echo(f"  excluded, {reason}: {count}")
    diagnostics.done(f"{len(rows)} shown, {sum(excluded.values())} excluded on {day.isoformat()}")
