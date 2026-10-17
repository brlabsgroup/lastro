"""Paths and runtime configuration.

The only configurable thing in Phase 1 is where the data lives. The default is
``data/`` at the repository root. Set the ``LASTRO_DATA_DIR`` environment
variable to point elsewhere (an external disk, a VPS volume). Changing it is
supported in the sense that the code honours it, and unsupported in every other
sense: whoever moves the data is responsible for it.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_DATA_DIR = "LASTRO_DATA_DIR"

# Repository root: the directory that holds pyproject.toml, two levels above this file.
REPO_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Return the data directory, honouring LASTRO_DATA_DIR when set."""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "data"


def raw_dir() -> Path:
    """Zips exactly as downloaded from the archive."""
    return data_dir() / "raw"


def parquet_dir() -> Path:
    """Converted market data, partitioned."""
    return data_dir() / "parquet"


def db_path() -> Path:
    """The operational SQLite database."""
    return data_dir() / "lastro.db"


def manifest_path() -> Path:
    """File list and hashes of the frozen dataset."""
    return data_dir() / "manifest.json"


def ensure_dirs() -> Path:
    """Create the data directory tree if it does not exist and return its root."""
    root = data_dir()
    for path in (root, raw_dir(), parquet_dir()):
        path.mkdir(parents=True, exist_ok=True)
    return root
