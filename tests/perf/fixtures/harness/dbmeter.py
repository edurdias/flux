"""Differential database meter (PLAN.md changelog-3).

The server runs as a subprocess, so nothing can attach an in-process
SQLAlchemy listener to its engine — and hosting the server in-process would
share the GIL with the load generator and corrupt CPU measurements. Instead
the meter opens the SQLite file read-only as a side channel and diffs
snapshots taken around a measurement window:

- per-table row counts,
- persisted event rows per execution, grouped by event type,
- database file size (main + WAL + journal).

The sharp T0 assertion is differential: runs emitting 100 and 5,000 progress
frames must leave identical persisted-event footprints.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DbSnapshot:
    taken_at: float
    row_counts: dict[str, int]
    file_bytes: int

    def total_rows(self) -> int:
        return sum(self.row_counts.values())


def diff(before: DbSnapshot, after: DbSnapshot) -> dict:
    """Row-count and size deltas between two snapshots (after - before)."""
    tables = sorted(set(before.row_counts) | set(after.row_counts))
    deltas = {t: after.row_counts.get(t, 0) - before.row_counts.get(t, 0) for t in tables}
    return {
        "rows": {t: d for t, d in deltas.items() if d != 0},
        "total_rows": after.total_rows() - before.total_rows(),
        "file_bytes": after.file_bytes - before.file_bytes,
        "window_s": after.taken_at - before.taken_at,
    }


class SqliteDbMeter:
    """Read-only differential meter over a live server's SQLite file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro",
            uri=True,
            timeout=10.0,
        )
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _file_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-journal"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def snapshot(self) -> DbSnapshot:
        with self._connect() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'",
                )
            ]
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables}
        return DbSnapshot(
            taken_at=time.time(),
            row_counts=counts,
            file_bytes=self._file_bytes(),
        )

    def execution_event_rows(self, execution_id: str) -> dict[str, int]:
        """Persisted event rows for one execution, grouped by event type."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) FROM execution_events WHERE execution_id = ? GROUP BY type",
                (execution_id,),
            ).fetchall()
        return {t: n for t, n in rows}

    def count_event_type(self, event_type: str) -> int:
        """Rows of a given event type across ALL executions."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM execution_events WHERE type = ?",
                (event_type,),
            ).fetchone()[0]
