"""The ``lastro`` command.

Phase 1 exposes ``doctor``, ``db init`` and ``db status``. The ``data`` and
``universe`` groups exist so the help output already shows the shape of the
pipeline; their commands land piece by piece and say so until then.

Every command ends with one result line (``ok      ...`` or ``FAILED  ...``),
and every failure says what broke, why, and what to do. See ``diagnostics``.
"""

from __future__ import annotations

import typer

from lastro import __version__, config, diagnostics
from lastro.db import store

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


@data_app.command("fetch")
def data_fetch() -> None:
    """Enumerate the archive and download missing files. (piece 2 and 3)"""
    _not_yet(2)


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
