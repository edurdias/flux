from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Column, MetaData, String, Table, Text, create_engine, delete, distinct, select
from sqlalchemy.engine import Engine


_metadata = MetaData()

_memory_table = Table(
    "memory",
    _metadata,
    Column("workflow", String, primary_key=True),
    Column("scope", String, primary_key=True),
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
)


class SqlAlchemyProvider:
    def __init__(self, url: str) -> None:
        self._url = url
        self._engine: Engine | None = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self._url)
            _metadata.create_all(self._engine)
        return self._engine

    async def memorize(self, workflow: str, scope: str, key: str, value: Any) -> None:
        engine = self._get_engine()
        serialized = json.dumps(value)
        with engine.begin() as conn:
            existing = conn.execute(
                select(_memory_table).where(
                    _memory_table.c.workflow == workflow,
                    _memory_table.c.scope == scope,
                    _memory_table.c.key == key,
                ),
            ).fetchone()
            if existing:
                conn.execute(
                    _memory_table.update()
                    .where(
                        _memory_table.c.workflow == workflow,
                        _memory_table.c.scope == scope,
                        _memory_table.c.key == key,
                    )
                    .values(value=serialized),
                )
            else:
                conn.execute(
                    _memory_table.insert().values(
                        workflow=workflow, scope=scope, key=key, value=serialized,
                    ),
                )

    async def recall(self, workflow: str, scope: str, key: str | None = None) -> Any:
        engine = self._get_engine()
        with engine.connect() as conn:
            if key is not None:
                row = conn.execute(
                    select(_memory_table.c.value).where(
                        _memory_table.c.workflow == workflow,
                        _memory_table.c.scope == scope,
                        _memory_table.c.key == key,
                    ),
                ).fetchone()
                return json.loads(row[0]) if row else None
            rows = conn.execute(
                select(_memory_table.c.key, _memory_table.c.value).where(
                    _memory_table.c.workflow == workflow,
                    _memory_table.c.scope == scope,
                ),
            ).fetchall()
            return {row[0]: json.loads(row[1]) for row in rows}

    async def forget(self, workflow: str, scope: str, key: str | None = None) -> None:
        engine = self._get_engine()
        with engine.begin() as conn:
            if key is not None:
                conn.execute(
                    delete(_memory_table).where(
                        _memory_table.c.workflow == workflow,
                        _memory_table.c.scope == scope,
                        _memory_table.c.key == key,
                    ),
                )
            else:
                conn.execute(
                    delete(_memory_table).where(
                        _memory_table.c.workflow == workflow,
                        _memory_table.c.scope == scope,
                    ),
                )

    async def keys(self, workflow: str, scope: str) -> list[str]:
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                select(_memory_table.c.key).where(
                    _memory_table.c.workflow == workflow,
                    _memory_table.c.scope == scope,
                ),
            ).fetchall()
            return [row[0] for row in rows]

    async def scopes(self, workflow: str) -> list[str]:
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                select(distinct(_memory_table.c.scope)).where(
                    _memory_table.c.workflow == workflow,
                ),
            ).fetchall()
            return [row[0] for row in rows]
