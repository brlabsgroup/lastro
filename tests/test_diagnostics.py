from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from lastro import config, diagnostics
from lastro.cli import app
from lastro.db import store


@pytest.fixture
def data_dir(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))
    return config.data_dir()


def test_python_check_passes_on_this_interpreter():
    result = diagnostics.check_python()
    assert result.ok


def test_data_dir_check_reports_unwritable(monkeypatch, tmp_path: Path):
    """Simulate the OS refusing the write; chmod-based tests lie when run as root."""
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "data"))

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_text", refuse)
    result = diagnostics.check_data_dir()
    assert not result.ok
    assert "Permission denied" in result.detail
    assert config.ENV_DATA_DIR in (result.fix or "")


def test_database_check_before_and_after_init(data_dir: Path):
    before = diagnostics.check_database()
    assert not before.ok
    assert "lastro db init" in (before.fix or "")

    conn, _ = store.init()
    conn.close()
    after = diagnostics.check_database()
    assert after.ok
    assert "schema version 1" in after.detail


def test_dependencies_check_passes_in_dev_env():
    result = diagnostics.check_dependencies()
    assert result.ok, result.detail


def test_network_check_handles_connection_error(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("simulated outage")

    monkeypatch.setattr(httpx, "head", boom)
    result = diagnostics.check_network()
    assert not result.ok
    assert "unreachable" in result.detail
    assert result.fix


def test_network_check_handles_bad_status(monkeypatch):
    monkeypatch.setattr(httpx, "head", lambda *a, **k: httpx.Response(503))
    result = diagnostics.check_network()
    assert not result.ok
    assert "503" in result.detail


def test_network_check_passes_on_200(monkeypatch):
    monkeypatch.setattr(httpx, "head", lambda *a, **k: httpx.Response(200))
    result = diagnostics.check_network()
    assert result.ok


def test_render_counts_and_exit_code():
    results = [
        diagnostics.Check("a", True, "fine"),
        diagnostics.Check("b", False, "broken", fix="do this"),
    ]
    text, code = diagnostics.render(results)
    assert code == 1
    assert "1 checks passed, 1 failed" in text
    assert "fix: do this" in text

    text, code = diagnostics.render([diagnostics.Check("a", True, "fine")])
    assert code == 0
    assert "1 checks passed, 0 failed" in text


def test_cli_doctor_offline_fails_before_init_and_passes_after(data_dir: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--offline"])
    assert result.exit_code == 1, result.output
    assert "FAIL  database" in result.output
    assert "run `lastro db init`" in result.output
    assert "network" not in result.output

    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["doctor", "--offline"])
    assert result.exit_code == 0, result.output
    assert "4 checks passed, 0 failed" in result.output


def test_fail_prints_three_parts_and_exits():
    result = CliRunner().invoke(app, ["data", "convert"])
    assert result.exit_code == 2
    assert "FAILED  this command" in result.output
    assert "why:" in result.output
    assert "fix:" in result.output
