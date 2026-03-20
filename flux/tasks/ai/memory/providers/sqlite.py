from __future__ import annotations

import json
import sqlite3
from typing import Any


class SqliteProvider:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    async def initialize(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                workflow   TEXT NOT NULL,
                scope      TEXT NOT NULL,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (workflow, scope, key)
            )
            """,
        )
        self._conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                    workflow   TEXT NOT NULL,
                    scope      TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workflow, scope, key)
                )
                """,
            )
            self._conn.commit()
        return self._conn

    async def memorize(self, workflow: str, scope: str, key: str, value: Any) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO memory (workflow, scope, key, value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (workflow, scope, key)
            DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (workflow, scope, key, json.dumps(value)),
        )
        conn.commit()

    async def recall(self, workflow: str, scope: str, key: str | None = None) -> Any:
        conn = self._get_conn()
        if key is not None:
            row = conn.execute(
                "SELECT value FROM memory WHERE workflow = ? AND scope = ? AND key = ?",
                (workflow, scope, key),
            ).fetchone()
            return json.loads(row[0]) if row else None
        rows = conn.execute(
            "SELECT key, value FROM memory WHERE workflow = ? AND scope = ?",
            (workflow, scope),
        ).fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    async def forget(self, workflow: str, scope: str, key: str | None = None) -> None:
        conn = self._get_conn()
        if key is not None:
            conn.execute(
                "DELETE FROM memory WHERE workflow = ? AND scope = ? AND key = ?",
                (workflow, scope, key),
            )
        else:
            conn.execute(
                "DELETE FROM memory WHERE workflow = ? AND scope = ?",
                (workflow, scope),
            )
        conn.commit()

    async def keys(self, workflow: str, scope: str) -> list[str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT key FROM memory WHERE workflow = ? AND scope = ?",
            (workflow, scope),
        ).fetchall()
        return [row[0] for row in rows]

    async def scopes(self, workflow: str) -> list[str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT scope FROM memory WHERE workflow = ?",
            (workflow,),
        ).fetchall()
        return [row[0] for row in rows]
