from pathlib import Path

import pytest
from typer.testing import CliRunner

from lastro import config
from lastro.cli import app
from lastro.db import store

EXPECTED_TABLES = {
    "archive_files",
    "symbols",
    "quality_issues",
    "universe",
    "restrictions",
    "dataset_manifest",
    "runs",
    "schema_version",
}


@pytest.fixture
def data_dir(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    return config.data_dir()


def test_init_creates_all_tables(data_dir: Path):
    conn, applied = store.init()
    try:
        assert applied == ["001_initial.sql"]
        assert set(store.user_tables(conn)) == EXPECTED_TABLES
        assert store.applied_versions(conn) == [1]
        assert all(count == 0 for name, count in store.table_counts(conn).items() if name != "schema_version")
    finally:
        conn.close()


def test_init_twice_applies_nothing_new(data_dir: Path):
    conn, first = store.init()
    conn.close()
    conn, second = store.init()
    try:
        assert first == ["001_initial.sql"]
        assert second == []
        assert store.applied_versions(conn) == [1]
        assert set(store.user_tables(conn)) == EXPECTED_TABLES
    finally:
        conn.close()


def test_transaction_rolls_back_on_error(data_dir: Path):
    conn, _ = store.init()
    try:
        with pytest.raises(RuntimeError):
            with store.transaction(conn):
                conn.execute(
                    "INSERT INTO runs (command, started_at) VALUES (?, ?)", ("x", "2026-01-01T00:00:00+00:00")
                )
                raise RuntimeError("boom")
        assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
    finally:
        conn.close()


def test_failed_migration_leaves_no_trace(data_dir: Path, monkeypatch):
    """A migration that fails halfway must not leave partial tables or a version row."""
    broken = [
        (1, "001_initial.sql", "CREATE TABLE half_done (id INTEGER); CREATE TABLE oops (id INTEGER PRIMARY KEY, id INTEGER);")
    ]
    monkeypatch.setattr(store, "_migration_files", lambda: broken)
    conn = store.connect()
    try:
        with pytest.raises(Exception):
            store.migrate(conn)
        assert "half_done" not in store.user_tables(conn)
        assert store.applied_versions(conn) == []
    finally:
        conn.close()


def test_statement_splitter_ignores_comments():
    sql = "CREATE TABLE a (x TEXT); -- trailing; comment\n-- whole line; comment\nCREATE TABLE b (y TEXT);\n"
    assert store._statements(sql) == ["CREATE TABLE a (x TEXT)", "CREATE TABLE b (y TEXT)"]


def test_cli_init_and_status(data_dir: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0, result.output
    assert "ok      database initialized, applied 001_initial.sql" in result.output

    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0, result.output
    assert "ok      database already current" in result.output

    result = runner.invoke(app, ["db", "status"])
    assert result.exit_code == 0, result.output
    assert "schema version: 1" in result.output
    for name in EXPECTED_TABLES:
        assert name in result.output
    assert "ok      8 tables, schema version 1" in result.output


def test_cli_status_without_db_fails_cleanly(data_dir: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["db", "status"])
    assert result.exit_code == 1
    assert "FAILED  reading the database" in result.output
    assert "fix: run `lastro db init`" in result.output


def test_cli_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("lastro ")


def test_unimplemented_commands_say_so():
    result = CliRunner().invoke(app, ["data", "freeze"])
    assert result.exit_code == 2
    assert "not implemented yet" in result.output
    assert "piece 8" in result.output
