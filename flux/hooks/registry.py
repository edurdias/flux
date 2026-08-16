"""Hook registry: a cached enabled-hook snapshot, plus CRUD.

``matches``/``has_any`` sit on the write hot path (Task 4's enqueue calls
``has_any()`` on every save, and ``matches()`` when it is true), so the
enabled-hook index is cached rather than queried per event. The cache is
module-level -- not an attribute on ``HookRegistry`` -- because
``HookRegistry.create()`` mirrors ``ContextManager.create()`` in handing back
a fresh accessor bound to the configured repository each call; sharing the
snapshot at module scope means every accessor sees the same cache and every
CRUD write (through any accessor) invalidates it for all of them.

That invalidation is process-local: a peer server replica's create/update/
delete never reaches this process's cache, since nothing broadcasts across
replicas. Left unbounded, a stale snapshot could keep firing a hook another
replica had already disabled or deleted -- a correctness gap, not just a
performance one, given hooks gate what auto-fires on production events. So
staleness is bounded by ``[flux.hooks] snapshot_ttl_seconds`` (default 5s):
``snapshot()`` rebuilds once the cached copy is older than the TTL, on top
of the immediate CRUD invalidation that keeps a *local* write's own replica
consistent without waiting out the TTL.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError

from flux.config import Configuration
from flux.errors import HookNameConflictError, HookNotFoundError
from flux.hooks.selectors import HookEvent, selector_matches, validate_selector
from flux.models import HookModel, RepositoryFactory

# Fields update_hook is allowed to touch. Deliberately excludes name/action/
# owner_type/owner_ref/created_by/created_at -- identity and provenance are
# immutable once a hook exists.
_UPDATABLE_FIELDS = frozenset(
    {"enabled", "selectors", "workflow_ref", "principal", "max_attempts"},
)


@dataclass(frozen=True)
class HookIndexEntry:
    id: str
    name: str
    selectors: tuple[str, ...]
    workflow_ref: str
    principal: str
    max_attempts: int
    owner_type: str
    owner_ref: str


_snapshot_lock = threading.Lock()
_snapshot: tuple[HookIndexEntry, ...] | None = None
# time.monotonic() timestamp of the last rebuild -- wall-clock-adjustment-proof,
# unlike time.time(), since it only ever needs to measure elapsed staleness.
_snapshot_loaded_at: float | None = None


class HookRegistry:
    @classmethod
    def create(cls) -> HookRegistry:
        return cls()

    def __init__(self):
        self._repository = RepositoryFactory.create_repository()

    def snapshot(self) -> tuple[HookIndexEntry, ...]:
        global _snapshot, _snapshot_loaded_at
        ttl = Configuration.get().settings.hooks.snapshot_ttl_seconds
        with _snapshot_lock:
            fresh = (
                _snapshot is not None
                and _snapshot_loaded_at is not None
                and ttl > 0
                and (time.monotonic() - _snapshot_loaded_at) < ttl
            )
            if not fresh:
                _snapshot = self._load_snapshot()
                _snapshot_loaded_at = time.monotonic()
            # mypy can't narrow a `global` across the branch above; `fresh`
            # being True already implies `_snapshot is not None`, and the
            # branch just set it otherwise.
            assert _snapshot is not None
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
                    principal=row.principal,
                    max_attempts=row.max_attempts,
                    owner_type=row.owner_type,
                    owner_ref=row.owner_ref,
                )
                for row in rows
            )

    def invalidate(self) -> None:
        global _snapshot, _snapshot_loaded_at
        with _snapshot_lock:
            _snapshot = None
            _snapshot_loaded_at = None

    def matches(self, event: HookEvent) -> list[HookIndexEntry]:
        # `any(...)` collapses a hook's several selectors into a single
        # match -- an OR across selectors, not a fan-out of deliveries.
        return [
            entry
            for entry in self.snapshot()
            if any(selector_matches(selector, event.key) for selector in entry.selectors)
            and self._owner_permits(entry, event)
        ]

    @staticmethod
    def _owner_permits(entry: HookIndexEntry, event: HookEvent) -> bool:
        """Agent-owned hooks carry a runtime backstop: their selector text
        cannot discriminate between agents (every session runs
        agents/agent_chat), so ownership is enforced here instead. Every
        other owner type relies on its selector text alone -- workflow-
        declared hooks are scope-confined at registration, user-declared
        hooks are meant to range freely."""
        if entry.owner_type != "agent":
            return True
        return event.agent == entry.owner_ref

    def has_any(self) -> bool:
        return len(self.snapshot()) > 0

    def create_hook(
        self,
        *,
        name: str,
        selectors: Sequence[str],
        workflow_ref: str,
        principal: str,
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
                principal=principal,
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

    def list_owned_hooks(self, *, owner_type: str, owner_ref: str) -> list[HookModel]:
        with self._repository.session() as session:
            return (
                session.query(HookModel)
                .filter_by(owner_type=owner_type, owner_ref=owner_ref)
                .order_by(HookModel.name)
                .all()
            )

    @staticmethod
    def _derive_hook_name(*, owner_type: str, owner_ref: str, spec: dict, suffix: str) -> str:
        """Derive a stable, owner-qualified name for a spec that has none.

        Two properties the derivation must hold, each fixing a distinct bug:

        - Owner-qualified: the full ``owner_type``/``owner_ref`` pair goes
          into the name, not just ``owner_ref``'s trailing path segment.
          ``HookModel.name`` is globally unique, so two owners whose ref
          happens to share a trailing segment (two namespaces' same-named
          workflow, or a workflow and an agent sharing a name) would
          otherwise derive the identical name and the second's create would
          fail the unique constraint.
        - Content-derived, not position-derived: the suffix is a digest of
          the spec's identity, not its index in the ``specs`` list. Keying
          on position meant removing or reordering an earlier spec shifted
          every later spec's derived name, silently handing a still-
          declared spec's row (and delivery history) to whatever spec now
          landed on that name.

        The digest covers ``on`` alone -- not ``workflow``/``principal`` too.
        A hook's identity, from the declarer's perspective, is "the thing
        watching this event": ``HookRegistry.matches()`` itself indexes
        hooks by selector, so this mirrors how the registry already thinks
        about them. Re-pointing an unnamed hook at a different target
        workflow or running it as a different principal, while it keeps
        watching the same event, is naturally an edit to the *same* hook --
        it should keep its row and delivery history, not fork into a new
        one. Only a changed ``on`` (or an owner/position change already
        covered above) is a new identity. A hook that must keep a stable
        name across an edited selector needs an explicit ``name=``.
        """
        owner_key = f"{owner_type}_{owner_ref}".replace("/", "_")
        digest = hashlib.sha256(spec["on"].encode()).hexdigest()[:12]
        return f"{owner_key}{suffix}_{digest}"

    def reconcile_owned_hooks(
        self,
        *,
        owner_type: str,
        owner_ref: str,
        specs: Sequence[dict],
        created_by: str | None = None,
    ) -> list[HookModel]:
        """Create-or-replace-by-derived-name; delete rows no longer declared.

        Keying on name -- not wiping and recreating every owned row -- is
        what lets a hook that is still declared keep its delivery history
        across a redeploy. Only rows for specs that disappeared from the
        declaration are removed, and only they lose their deliveries (via
        the ``hook_deliveries.hook_id`` FK cascade).
        """
        suffix = Configuration.get().settings.hooks.auto_hook_suffix

        desired: dict[str, dict] = {}
        for spec in specs:
            name = spec.get("name") or self._derive_hook_name(
                owner_type=owner_type,
                owner_ref=owner_ref,
                spec=spec,
                suffix=suffix,
            )
            desired[name] = spec

        existing = {
            row.name: row
            for row in self.list_owned_hooks(owner_type=owner_type, owner_ref=owner_ref)
        }

        result = []
        for name, spec in desired.items():
            fields = {
                "selectors": [spec["on"]],
                "workflow_ref": spec["workflow"],
                "principal": spec["principal"],
                "max_attempts": spec.get("max_attempts", 5),
            }
            if name in existing:
                result.append(self.update_hook(name, **fields))
            else:
                try:
                    result.append(
                        self.create_hook(
                            name=name,
                            selectors=fields["selectors"],
                            workflow_ref=fields["workflow_ref"],
                            principal=fields["principal"],
                            owner_type=owner_type,
                            owner_ref=owner_ref,
                            max_attempts=fields["max_attempts"],
                            created_by=created_by,
                        ),
                    )
                except IntegrityError as e:
                    raise HookNameConflictError(name) from e

        for stale_name in set(existing) - set(desired):
            self.delete_hook(stale_name)

        return result

    def delete_owned_hooks(self, *, owner_type: str, owner_ref: str) -> int:
        """Delete every hook this owner declared -- workflow delete / agent delete."""
        count = 0
        for row in self.list_owned_hooks(owner_type=owner_type, owner_ref=owner_ref):
            self.delete_hook(row.name)
            count += 1
        return count
