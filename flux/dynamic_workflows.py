"""Agent-authored dynamic workflows: ephemeral registration (spec:
docs/specs/2026-07-15-dynamic-workflows-spec.md).

Dynamic workflows are ordinary workflows — same catalog, dispatch, replay,
cancellation, and approval machinery. What this module owns is the
*registration policy* for model-authored source: where it may live
(per-principal ``dyn-*`` namespaces), what it may declare (a with_options
allowlist — no schedules, no services, no resource requests), how it is
contained (the isolation runner stamped server-side, since the author is
the adversary and AST-extracted options cannot be the enforcement point),
and for how long it is kept (last-used TTL, never collected under a live
execution).

The source is parsed with the catalog's exec-free static parser only —
hostile source must never run on the server.
"""

from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from flux._namespace import RESERVED_DYNAMIC_PREFIX
from flux.utils import get_logger

logger = get_logger(__name__)

# with_options keywords a dynamic workflow may declare. Everything else is
# rejected with a structured error: schedule= would install standing
# recurring execution from a one-shot authoring call; requests=/affinity=/
# routing= are owned by the stamped isolation profile and placement;
# namespace= is owned by the server; runner= is stamped.
_ALLOWED_WITH_OPTIONS = frozenset(
    {"name", "durability", "secret_requests", "output_storage"},
)

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_-]+")


class DynamicRegistrationError(ValueError):
    """Policy rejection with a message the authoring agent can act on."""


def namespace_for_subject(subject: str) -> str:
    """Deterministic per-principal namespace: ``dyn-<slug>-<hash8>``.

    Derived server-side from the execution token's subject — never from the
    request — so one principal cannot write into another's namespace by
    construction. The hash suffix keeps distinct subjects distinct even when
    slugging collides (e.g. ``Agent.A`` vs ``agent-a``).
    """
    slug = _SLUG_STRIP_RE.sub("-", subject.lower()).strip("-") or "agent"
    digest = hashlib.sha256(subject.encode()).hexdigest()[:8]
    # namespace max is 64; prefix(4) + hash(8) + separator(1)
    return f"{RESERVED_DYNAMIC_PREFIX}{slug[:48]}-{digest}"


def source_hash(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()[:16]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_policy(source: bytes) -> ast.AsyncFunctionDef:
    """Static policy validation of dynamic workflow source.

    Returns the single workflow's AST node. Raises DynamicRegistrationError
    with an actionable message on any violation. Never executes the source.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise DynamicRegistrationError(f"syntax error: {e}") from e

    workflow_nodes: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "workflow":
                workflow_nodes.append(node)
            elif (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "workflow"
                and decorator.func.attr == "with_options"
            ):
                workflow_nodes.append(node)
                for kw in decorator.keywords:
                    if kw.arg not in _ALLOWED_WITH_OPTIONS:
                        raise DynamicRegistrationError(
                            f"with_options '{kw.arg}' is not allowed in a dynamic "
                            f"workflow (allowed: {', '.join(sorted(_ALLOWED_WITH_OPTIONS))}). "
                            "Schedules, services, resource requests, affinity, routing, "
                            "namespace, and runner are owned by the platform.",
                        )

    if len(workflow_nodes) != 1:
        raise DynamicRegistrationError(
            f"source must define exactly one @workflow (found {len(workflow_nodes)})",
        )
    return workflow_nodes[0]


def register(
    source: bytes,
    *,
    subject: str,
    config,
) -> dict[str, Any]:
    """Validate and register dynamic workflow source for ``subject``.

    Returns ``{"namespace", "name", "version", "existing"}``. Idempotent by
    source hash: byte-identical re-registration returns the current entry
    (refreshing ``last_used_at``) without a version bump, so replay and the
    source-hash-keyed module cache stay warm.
    """
    from flux.catalogs import WorkflowCatalog

    if len(source) > config.max_source_bytes:
        raise DynamicRegistrationError(
            f"source is {len(source)} bytes; the dynamic registration cap is "
            f"{config.max_source_bytes} bytes",
        )

    validate_policy(source)

    catalog = WorkflowCatalog.create()
    # Static parse only: metadata that requires importing the module (input
    # schema) stays unpopulated — hostile source never executes on the server.
    infos = catalog.parse_static(source)
    if len(infos) != 1:  # pragma: no cover — validate_policy already enforces
        raise DynamicRegistrationError(
            f"source must define exactly one @workflow (found {len(infos)})",
        )
    info = infos[0]

    namespace = namespace_for_subject(subject)
    digest = source_hash(source)

    existing = _latest(catalog, namespace, info.name)
    if existing is not None:
        existing_meta = (existing.metadata or {}).get("dynamic") or {}
        if existing_meta.get("source_hash") == digest:
            touch_last_used(namespace, existing.name)
            return {
                "namespace": namespace,
                "name": existing.name,
                "version": existing.version,
                "existing": True,
            }
    else:
        count = _distinct_names(namespace)
        if count >= config.max_per_agent:
            raise DynamicRegistrationError(
                f"namespace '{namespace}' already holds {count} workflows "
                f"(max_per_agent={config.max_per_agent}); delete unused ones "
                "or reuse an existing name",
            )

    info.namespace = namespace
    metadata = dict(info.metadata or {})
    # The stamp: the author is the adversary, so the isolation runner comes
    # from server config, not from anything the source declared.
    metadata["runner"] = config.require_runner
    metadata["dynamic"] = {
        "source_hash": digest,
        "created_by": subject,
        "created_at": _utcnow_iso(),
        "last_used_at": _utcnow_iso(),
    }
    info.metadata = metadata

    saved = catalog.save([info])[0]
    logger.info(
        f"Dynamic workflow registered: {namespace}/{saved.name} v{saved.version} "
        f"by '{subject}' (runner={config.require_runner})",
    )
    return {
        "namespace": namespace,
        "name": saved.name,
        "version": saved.version,
        "existing": False,
    }


def touch_last_used(namespace: str, name: str) -> None:
    """Refresh the GC clock on a dynamic entry (latest version row)."""
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = (
            session.query(WorkflowModel)
            .filter(WorkflowModel.namespace == namespace, WorkflowModel.name == name)
            .order_by(WorkflowModel.version.desc())
            .first()
        )
        if model is None:
            return
        metadata = dict(model.wf_metadata or {})
        dynamic = dict(metadata.get("dynamic") or {})
        dynamic["last_used_at"] = _utcnow_iso()
        metadata["dynamic"] = dynamic
        model.wf_metadata = metadata
        session.commit()


def _latest(catalog, namespace: str, name: str):
    try:
        return catalog.get(namespace, name)
    except Exception:
        return None


def _distinct_names(namespace: str) -> int:
    from sqlalchemy import func as sa_func

    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        return (
            session.query(sa_func.count(sa_func.distinct(WorkflowModel.name)))
            .filter(WorkflowModel.namespace == namespace)
            .scalar()
        ) or 0


def gc_sweep(*, ttl_seconds: int) -> int:
    """Delete dynamic entries unused for longer than the TTL.

    Never collects a workflow with a non-terminal execution — the sweep is
    bounded by max_per_agent x agents, so loading candidates is cheap.
    Returns the number of workflow rows removed.
    """
    from flux.domain import ExecutionState
    from flux.models import ExecutionContextModel, RepositoryFactory, WorkflowModel

    if ttl_seconds <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    terminal = (
        ExecutionState.COMPLETED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
    )
    removed = 0
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        candidates = (
            session.query(WorkflowModel)
            .filter(WorkflowModel.namespace.like(f"{RESERVED_DYNAMIC_PREFIX}%"))
            .all()
        )
        for model in candidates:
            dynamic = (model.wf_metadata or {}).get("dynamic") or {}
            last_used = dynamic.get("last_used_at")
            if last_used is None:
                continue
            try:
                last_used_at = datetime.fromisoformat(last_used)
            except ValueError:
                continue
            if last_used_at.tzinfo is None:
                last_used_at = last_used_at.replace(tzinfo=timezone.utc)
            if last_used_at >= cutoff:
                continue
            live = (
                session.query(ExecutionContextModel.execution_id)
                .filter(
                    ExecutionContextModel.workflow_id == model.id,
                    ExecutionContextModel.state.notin_(terminal),
                )
                .first()
            )
            if live is not None:
                continue
            session.delete(model)
            removed += 1
        session.commit()
    if removed:
        logger.info(f"Dynamic workflow GC removed {removed} unused entries")
    return removed
