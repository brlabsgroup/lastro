"""Uniform result reporting and the environment checks behind ``lastro doctor``.

Every command ends with one result line, and every failure carries three parts:
what failed, why, and what to do about it. Nothing in Lastro raises a bare
error at the user; it goes through :func:`fail` or returns a :class:`Check`.
"""

from __future__ import annotations

import importlib.metadata
import sys
import time
from dataclasses import dataclass
from typing import Callable

import typer

from lastro import config

ARCHIVE_PROBE_URL = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip"
MIN_PYTHON = (3, 12)
REQUIRED_PACKAGES = ("duckdb", "pyarrow", "httpx", "typer", "pydantic")


@dataclass(frozen=True)
class Check:
    """One line of ``lastro doctor``."""

    name: str
    ok: bool
    detail: str
    fix: str | None = None


def fail(what: str, why: str, fix: str, code: int = 1) -> None:
    """Print a three-part failure and exit. Every user-facing error goes through here."""
    typer.echo(f"FAILED  {what}", err=True)
    typer.echo(f"        why: {why}", err=True)
    typer.echo(f"        fix: {fix}", err=True)
    raise typer.Exit(code=code)


def done(summary: str) -> None:
    """Print the one-line success result every command ends with."""
    typer.echo(f"ok      {summary}")


def check_python() -> Check:
    found = sys.version_info[:3]
    text = ".".join(str(part) for part in found)
    required = ".".join(str(part) for part in MIN_PYTHON)
    if found[:2] >= MIN_PYTHON:
        return Check("python", True, f"{text} (>= {required} required)")
    return Check(
        "python", False, f"{text} is below the required {required}",
        fix=f"install Python {required} or newer and recreate the venv: python3 -m venv .venv",
    )


def check_data_dir() -> Check:
    root = config.data_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            "data dir", False, f"{root} is not writable ({exc.strerror or exc})",
            fix=f"fix the permissions, or point {config.ENV_DATA_DIR} at a directory you can write to",
        )
    return Check("data dir", True, f"writable: {root}")


def check_database() -> Check:
    from lastro.db import store  # local import: keep diagnostics importable even if the db layer breaks

    path = config.db_path()
    if not path.exists():
        return Check("database", False, f"not found at {path}", fix="run `lastro db init`")
    try:
        conn = store.connect(path)
        try:
            versions = store.applied_versions(conn)
            tables = store.user_tables(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - any failure here is a diagnostic result, not a crash
        return Check(
            "database", False, f"cannot open {path} ({exc})",
            fix="if the file is corrupt, move it aside and run `lastro db init` again (Phase 1 data is rebuildable)",
        )
    if not versions:
        return Check("database", False, f"{path} exists but has no schema", fix="run `lastro db init`")
    return Check("database", True, f"schema version {versions[-1]}, {len(tables)} tables")


def check_dependencies() -> Check:
    found: list[str] = []
    missing: list[str] = []
    for name in REQUIRED_PACKAGES:
        try:
            found.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return Check(
            "dependencies", False, "missing: " + ", ".join(missing),
            fix='run `pip install -e ".[dev]"` inside the venv',
        )
    return Check("dependencies", True, ", ".join(found))


def check_network(timeout: float = 10.0) -> Check:
    import httpx  # local import so the check reports a missing httpx instead of crashing on import

    started = time.perf_counter()
    try:
        response = httpx.head(ARCHIVE_PROBE_URL, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        return Check(
            "network", False, f"data.binance.vision unreachable ({exc.__class__.__name__}: {exc})",
            fix="check your connection or proxy; the archive is a public bucket, no key needed",
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        return Check(
            "network", False, f"data.binance.vision answered HTTP {response.status_code} for a known file",
            fix="the archive may be having issues; retry later, and if it persists check for a proxy rewriting requests",
        )
    return Check("network", True, f"data.binance.vision reachable (HTTP 200, {elapsed_ms} ms)")


ALL_CHECKS: tuple[Callable[[], Check], ...] = (
    check_python,
    check_data_dir,
    check_database,
    check_dependencies,
    check_network,
)


def run_checks(checks: tuple[Callable[[], Check], ...] = ALL_CHECKS) -> list[Check]:
    return [check() for check in checks]


def render(results: list[Check]) -> tuple[str, int]:
    """Format doctor output. Returns (text, exit_code)."""
    width = max(len(result.name) for result in results)
    lines = []
    for result in results:
        tag = "ok  " if result.ok else "FAIL"
        lines.append(f"  {tag}  {result.name.ljust(width)}  {result.detail}")
        if not result.ok and result.fix:
            lines.append(f"        fix: {result.fix}")
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed
    lines.append("")
    lines.append(f"{passed} checks passed, {failed} failed")
    return "\n".join(lines), (0 if failed == 0 else 1)
