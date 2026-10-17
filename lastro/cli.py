"""The ``lastro`` command.

Phase 1 exposes ``doctor``, ``db init`` and ``db status``. The ``data`` and
``universe`` groups exist so the help output already shows the shape of the
pipeline; their commands land piece by piece and say so until then.

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


@data_app.command("fetch")
def data_fetch() -> None:
    """Download the files `data list` found and are still missing. (piece 3)"""
    _not_yet(3)


@data_app.command("verify")
def data_verify() -> None:
    """Check downloaded files against their published checksums. (piece 3)"""
    _not_yet(3)


@data_app.command("convert")
def data_convert() -> None:
    """Convert verified zips into partitioned Parquet. (piece 4)"""
    _not_yet(4)


@data_app.command("quality")
def data_quality() -> None:
    """Run the quality checks and record issues. (piece 5)"""
    _not_yet(5)


@data_app.command("freeze")
def data_freeze() -> None:
    """Write the dataset manifest and hash. (piece 8)"""
    _not_yet(8)


@universe_app.command("build")
def universe_build() -> None:
    """Build point-in-time eligibility. (piece 8)"""
    _not_yet(8)
