"""Hook registry: a cached enabled-hook snapshot, plus CRUD.

``matches``/``has_any`` sit on the write hot path (Task 4's enqueue calls
``has_any()`` on every save, and ``matches()`` when it is true), so the
enabled-hook index is cached rather than queried per event. The cache is
module-level -- not an attribute on ``HookRegistry`` -- because
``HookRegistry.create()`` mirrors ``ContextManager.create()`` in handing back
a fresh accessor bound to the configured repository each call; sharing the
snapshot at module scope means every accessor sees the same cache and every
CRUD write (through any accessor) invalidates it for all of them.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from flux.errors import HookNotFoundError
from flux.hooks.selectors import HookEvent, selector_matches, validate_selector
from flux.models import HookModel, RepositoryFactory

# Fields update_hook is allowed to touch. Deliberately excludes name/action/
# owner_type/owner_ref/created_by/created_at -- identity and provenance are
# immutable once a hook exists.
_UPDATABLE_FIELDS = frozenset(
    {"enabled", "selectors", "workflow_ref", "principal_id", "max_attempts"},
)


@dataclass(frozen=True)
class HookIndexEntry:
    id: str
    name: str
    selectors: tuple[str, ...]
    workflow_ref: str
    principal_id: str
    max_attempts: int


_snapshot_lock = threading.Lock()
_snapshot: tuple[HookIndexEntry, ...] | None = None


class HookRegistry:
    @classmethod
    def create(cls) -> HookRegistry:
        return cls()

    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def snapshot(self) -> tuple[HookIndexEntry, ...]:
        global _snapshot
        with _snapshot_lock:
            if _snapshot is None:
                _snapshot = self._load_snapshot()
            return _snapshot

    def _load_snapshot(self) -> tuple[HookIndexEntry, ...]:
        with self._repository.session() as session:
            rows = session.query(HookModel).filter_by(enabled=True).all()
            return tuple(
                HookIndexEntry(
                    id=row.id,
                    name=row.name,
                    selectors=tuple(row.selectors or []),
                    workflow_ref=row.workflow_ref,
                    principal_id=row.principal_id,
                    max_attempts=row.max_attempts,
                )
                for row in rows
            )

    def invalidate(self) -> None:
        global _snapshot
        with _snapshot_lock:
            _snapshot = None

    def matches(self, event: HookEvent) -> list[HookIndexEntry]:
        # `any(...)` collapses a hook's several selectors into a single
        # match -- an OR across selectors, not a fan-out of deliveries.
        return [
            entry
            for entry in self.snapshot()
            if any(selector_matches(selector, event.key) for selector in entry.selectors)
        ]

    def has_any(self) -> bool:
        return len(self.snapshot()) > 0

    def create_hook(
        self,
        *,
        name: str,
        selectors: Sequence[str],
        workflow_ref: str,
        principal_id: str,
        owner_type: str = "user",
        owner_ref: str,
        max_attempts: int = 5,
        created_by: str | None = None,
    ) -> HookModel:
        # Validate before touching the database: a bad selector must never
        # leave a partial row behind.
        for selector in selectors:
            validate_selector(selector)

        with self._repository.session() as session:
            hook = HookModel(
                name=name,
                selectors=list(selectors),
                workflow_ref=workflow_ref,
                principal_id=principal_id,
                owner_type=owner_type,
                owner_ref=owner_ref,
                max_attempts=max_attempts,
                created_by=created_by,
            )
            session.add(hook)
            # A duplicate name surfaces as the underlying IntegrityError --
            # routes map it to 409, so it is deliberately not caught here.
            session.commit()
            self.invalidate()
            session.refresh(hook)
            return hook

    def list_hooks(self, *, enabled_only: bool = False) -> list[HookModel]:
        with self._repository.session() as session:
            query = session.query(HookModel)
            if enabled_only:
                query = query.filter_by(enabled=True)
            return query.order_by(HookModel.name).all()

    def get_hook(self, name: str) -> HookModel:
        with self._repository.session() as session:
            hook = session.query(HookModel).filter_by(name=name).one_or_none()
            if hook is None:
                raise HookNotFoundError(name)
            return hook

    def update_hook(self, name: str, **fields: Any) -> HookModel:
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown hook field(s): {', '.join(sorted(unknown))}")

        if "selectors" in fields:
            for selector in fields["selectors"]:
                validate_selector(selector)
            fields["selectors"] = list(fields["selectors"])

        with self._repository.session() as session:
            hook = session.query(HookModel).filter_by(name=name).one_or_none()
            if hook is None:
                raise HookNotFoundError(name)

            for field_name, value in fields.items():
                setattr(hook, field_name, value)

            session.commit()
            self.invalidate()
            session.refresh(hook)
            return hook

    def delete_hook(self, name: str) -> None:
        with self._repository.session() as session:
            hook = session.query(HookModel).filter_by(name=name).one_or_none()
            if hook is None:
                raise HookNotFoundError(name)

            session.delete(hook)
            session.commit()
            self.invalidate()
