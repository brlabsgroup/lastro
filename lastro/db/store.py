"""Open the operational database and keep its schema current.

Migrations are plain SQL files in ``lastro/db/migrations/``, named ``NNN_name.sql``.
They are applied in numeric order, each inside one transaction, and recorded in
``schema_version``. A file that has already been applied is never run again, so
``init`` is safe to call any number of times.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterator

from lastro import config

MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the database (creating the file if needed) with sane defaults."""
    target = path or config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None)  # autocommit; transactions are explicit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside one transaction; roll back on any exception."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _migration_files() -> list[tuple[int, str, str]]:
    """Return (version, filename, sql) for every migration shipped with the package, sorted."""
    found: list[tuple[int, str, str]] = []
    package_dir = resources.files("lastro.db") / "migrations"
    for entry in package_dir.iterdir():
        match = MIGRATION_NAME.match(entry.name)
        if not match:
            continue
        found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort(key=lambda item: item[0])
    return found


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    _ensure_version_table(conn)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    return [row["version"] for row in rows]


def pending_versions(conn: sqlite3.Connection) -> list[str]:
    """Names of the shipped migrations this database has not applied yet."""
    done = set(applied_versions(conn))
    return [name for version, name, _ in _migration_files() if version not in done]


def _statements(sql: str) -> list[str]:
    """Split a migration file into individual statements.

    ``sqlite3.executescript`` would be simpler, but it issues an implicit COMMIT
    first, which breaks the atomicity we want per migration. Migration files
    use plain statements separated by semicolons, with ``--`` comments, so a
    comment-stripping split is enough.
    """
    lines = []
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0]
        if stripped.strip():
            lines.append(stripped)
    body = "\n".join(lines)
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every pending migration, each in its own transaction. Returns the names applied."""
    _ensure_version_table(conn)
    done = set(applied_versions(conn))
    applied: list[str] = []
    for version, name, sql in _migration_files():
        if version in done:
            continue
        with transaction(conn):
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, _utcnow()),
            )
        applied.append(name)
    return applied


def init(path: Path | None = None) -> tuple[sqlite3.Connection, list[str]]:
    """Open the database and bring it to the current schema."""
    conn = connect(path)
    applied = migrate(conn)
    return conn, applied


def user_tables(conn: sqlite3.Connection) -> list[str]:
    """Names of the tables that belong to Lastro (excludes SQLite internals)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per Lastro table."""
    counts: dict[str, int] = {}
    for name in user_tables(conn):
        counts[name] = conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()["n"]
    return counts
