from pathlib import Path

from lastro import config


def test_default_data_dir_is_repo_root_data(monkeypatch):
    monkeypatch.delenv(config.ENV_DATA_DIR, raising=False)
    assert config.data_dir() == config.REPO_ROOT / "data"
    assert config.db_path() == config.REPO_ROOT / "data" / "lastro.db"


def test_env_override_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "elsewhere"))
    assert config.data_dir() == (tmp_path / "elsewhere").resolve()
    assert config.raw_dir() == (tmp_path / "elsewhere" / "raw").resolve()


def test_ensure_dirs_creates_tree(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path / "d"))
    root = config.ensure_dirs()
    assert root.is_dir()
    assert (root / "raw").is_dir()
    assert (root / "parquet").is_dir()
    # Idempotent.
    assert config.ensure_dirs() == root
