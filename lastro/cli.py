"""The ``lastro`` command.

``doctor`` and the ``db`` group check and prepare the environment. The ``data``
group is the pipeline in order: ``list``, ``fetch``, ``verify``, ``convert``,
``quality``, ``freeze``. Commands that have not landed yet say so.

Every command ends with one result line (``ok      ...`` or ``FAILED  ...``),
and every failure says what broke, why, and what to do. See ``diagnostics``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import typer

from lastro import __version__, config, diagnostics
from lastro.db import store


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open_db():
    """Open the database for a command, failing with instructions if it is missing."""
    path = config.db_path()
    if not path.exists():
        diagnostics.fail("opening the database", f"no database at {path}", "run `lastro db init`")
    return store.connect(path)


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
universe_app = typer.Typer(help="Point-in-time universe.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(data_app, name="data")
app.add_typer(universe_app, name="universe")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lastro {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", help="Show the version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Root command. Subcommands do the work."""


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


def _not_yet(piece: int) -> None:
    diagnostics.fail(
        "this command",
        f"not implemented yet; it arrives in Phase 1, piece {piece}",
        "follow the build order in docs/phase-1-plan.md",
        code=2,
    )


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
def data_fetch(symbol: list[str] = _SYMBOL_OPTION, interval: list[str] = _INTERVAL_OPTION) -> None:
    """Download the pending files `data list` found. Resumable: files already on disk are skipped."""
    from lastro.data import fetch

    symbols, intervals = _filters(symbol, interval)
    params = {"symbols": symbols, "intervals": list(intervals) if intervals else None}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        result = fetch.fetch_pending(conn, symbols=symbols, intervals=intervals, progress=lambda m: typer.echo(f"  {m}"))
        elapsed = time.perf_counter() - clock
        summary = (
            f"{result.downloaded} downloaded, {result.skipped} already on disk, "
            f"{result.bytes / 1e6:.1f} MB, {elapsed:.0f} s"
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
    """Check downloaded files against the SHA-256 the archive publishes. Mismatches are deleted and re-queued."""
    from lastro.data import checksum

    symbols, intervals = _filters(symbol, interval)
    params = {"symbols": symbols, "intervals": list(intervals) if intervals else None}
    conn = _open_db()
    started_at = _utcnow()
    clock = time.perf_counter()
    try:
        result = checksum.verify_downloaded(conn, symbols=symbols, intervals=intervals, progress=lambda m: typer.echo(f"  {m}"))
        elapsed = time.perf_counter() - clock
        summary = f"{result.verified} verified, {len(result.failed)} failed, {elapsed:.0f} s"
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
        result = parquet.convert_verified(conn, symbols=symbols, intervals=intervals, progress=lambda m: typer.echo(f"  {m}"))
        elapsed = time.perf_counter() - clock
        summary = f"{result.files} files, {result.rows} rows into {result.partitions} partitions, {elapsed:.0f} s"
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
            diagnostics.fail(
                "quality checks",
                f"{len(result.hard_failures)} hard failures ({', '.join(f'{k} {v}' for k, v in hard.items())}); "
                "the Parquet cannot be used as it stands",
                "rebuild the affected pairs with `lastro data verify` and `lastro data convert -s <pair>`; "
                "if it persists the archive file itself is wrong: open an issue with the FAIL lines",
            )
        _record_run(conn, "data quality", params, started_at, "ok", summary)
    finally:
        conn.close()
    diagnostics.done(summary)


@data_app.command("freeze")
def data_freeze() -> None:
    """Write the dataset manifest and hash. (piece 8)"""
    _not_yet(8)


@universe_app.command("build")
def universe_build() -> None:
    """Build point-in-time eligibility. (piece 8)"""
    _not_yet(8)
