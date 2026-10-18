"""Paths and runtime configuration.

Two things are configurable in Phase 1, both through environment variables:

- ``LASTRO_DATA_DIR``: where the data lives. The default is ``data/`` at the
  repository root. Changing it is supported in the sense that the code honours
  it, and unsupported in every other sense: whoever moves the data is
  responsible for it.
- ``LASTRO_BINANCE_API``: base URL for Binance's public market-data REST API
  (exchange metadata, order book depth). The default is the market-data-only
  host, which needs no key and is not geo-restricted the way the main API is.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_DATA_DIR = "LASTRO_DATA_DIR"
ENV_BINANCE_API = "LASTRO_BINANCE_API"
DEFAULT_BINANCE_API = "https://data-api.binance.vision"

# Repository root: the directory that holds pyproject.toml, two levels above this file.
REPO_ROOT = Path(__file__).resolve().parent.parent


def binance_api() -> str:
    """Base URL of the public market-data API, honouring LASTRO_BINANCE_API when set."""
    return os.environ.get(ENV_BINANCE_API, DEFAULT_BINANCE_API).rstrip("/")


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
