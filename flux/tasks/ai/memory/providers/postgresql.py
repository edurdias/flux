from __future__ import annotations

import json
from typing import Any

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore[assignment]


class PostgresqlProvider:
    def __init__(self, connection_string: str) -> None:
        if psycopg2 is None:
            raise ImportError(
                "To use PostgreSQL memory, install psycopg2: pip install flux-core[postgresql]"
            )
        self._connection_string = connection_string
        self._conn = None

    async def initialize(self) -> None:
        self._conn = psycopg2.connect(self._connection_string)
        with self._conn.cursor() as cur:
            cur.execute(
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
                """
            )
        self._conn.commit()

    def _get_conn(self):
        if self._conn is None:
            raise RuntimeError("PostgresqlProvider not initialized. Call await provider.initialize() first.")
        return self._conn

    async def memorize(self, workflow: str, scope: str, key: str, value: Any) -> None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory (workflow, scope, key, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workflow, scope, key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                """,
                (workflow, scope, key, json.dumps(value)),
            )
        conn.commit()

    async def recall(self, workflow: str, scope: str, key: str | None = None) -> Any:
        conn = self._get_conn()
        with conn.cursor() as cur:
            if key is not None:
                cur.execute(
                    "SELECT value FROM memory WHERE workflow = %s AND scope = %s AND key = %s",
                    (workflow, scope, key),
                )
                row = cur.fetchone()
                return json.loads(row[0]) if row else None
            cur.execute(
                "SELECT key, value FROM memory WHERE workflow = %s AND scope = %s",
                (workflow, scope),
            )
            return {row[0]: json.loads(row[1]) for row in cur.fetchall()}

    async def forget(self, workflow: str, scope: str, key: str | None = None) -> None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            if key is not None:
                cur.execute(
                    "DELETE FROM memory WHERE workflow = %s AND scope = %s AND key = %s",
                    (workflow, scope, key),
                )
            else:
                cur.execute(
                    "DELETE FROM memory WHERE workflow = %s AND scope = %s",
                    (workflow, scope),
                )
        conn.commit()

    async def keys(self, workflow: str, scope: str) -> list[str]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key FROM memory WHERE workflow = %s AND scope = %s",
                (workflow, scope),
            )
            return [row[0] for row in cur.fetchall()]

    async def scopes(self, workflow: str) -> list[str]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT scope FROM memory WHERE workflow = %s",
                (workflow,),
            )
            return [row[0] for row in cur.fetchall()]
