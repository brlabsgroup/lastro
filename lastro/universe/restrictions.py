"""Dated restriction lists per jurisdiction, seeded from CSV files shipped with the package.

A restriction names an asset, a jurisdiction and the date it took effect (and,
once lifted, the date it ended). The loader expands each asset to every pair in
``symbols`` with that base asset, so the universe builder can join on the pair.
An asset with no known pair gets a placeholder ``<ASSET>USDT`` row, so a later
listing is covered too. Loading is idempotent.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from lastro.db import store

PACKAGED = ("restrictions_br.csv",)
COLUMNS = ("jurisdiction", "asset", "valid_from", "valid_to", "reason", "source")


class RestrictionsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Restriction:
    jurisdiction: str
    asset: str
    valid_from: str
    valid_to: str | None
    reason: str
    source: str


@dataclass
class SeedResult:
    restrictions: int = 0  # rows read from the CSV files
    pairs: int = 0  # rows written to the restrictions table
    placeholders: int = 0  # assets with no known pair


def packaged_files() -> list[Path]:
    folder = resources.files("lastro.universe")
    return [Path(str(folder / name)) for name in PACKAGED]


def read_csv(path: Path) -> list[Restriction]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise RestrictionsError(f"{path}: expected columns {', '.join(COLUMNS)}, found {reader.fieldnames}")
        rows = []
        for n, row in enumerate(reader, start=2):
            if not row["jurisdiction"] or not row["asset"] or not row["valid_from"]:
                raise RestrictionsError(f"{path} line {n}: jurisdiction, asset and valid_from are required")
            rows.append(Restriction(
                row["jurisdiction"].strip().upper(), row["asset"].strip().upper(), row["valid_from"].strip(),
                row["valid_to"].strip() or None, row["reason"].strip(), row["source"].strip(),
            ))
    return rows


def seed(conn: sqlite3.Connection, paths: list[Path] | None = None) -> SeedResult:
    """Load every CSV (the packaged ones by default) into the restrictions table."""
    result = SeedResult()
    pairs_by_base: dict[str, list[str]] = {}
    for row in conn.execute("SELECT symbol, base_asset FROM symbols").fetchall():
        pairs_by_base.setdefault(row["base_asset"], []).append(row["symbol"])
    with store.transaction(conn):
        for path in paths or packaged_files():
            for item in read_csv(path):
                result.restrictions += 1
                pairs = pairs_by_base.get(item.asset)
                if not pairs:
                    pairs = [item.asset + "USDT"]
                    result.placeholders += 1
                for pair in pairs:
                    conn.execute(
                        "INSERT INTO restrictions (jurisdiction, symbol, valid_from, valid_to, reason) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT (jurisdiction, symbol, valid_from) DO UPDATE SET valid_to = excluded.valid_to, reason = excluded.reason",
                        (item.jurisdiction, pair, item.valid_from, item.valid_to, item.reason),
                    )
                    result.pairs += 1
    return result
