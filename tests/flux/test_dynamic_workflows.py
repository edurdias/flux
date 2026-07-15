"""Dynamic workflow registration: policy, idempotency, quota, stamping, GC.

Spec: docs/specs/2026-07-15-dynamic-workflows-spec.md. Authorization gets
dedicated coverage in tests/security/test_dynamic_workflows_authz.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from flux._namespace import validate_namespace
from flux.dynamic_workflows import (
    DynamicRegistrationError,
    gc_sweep,
    namespace_for_subject,
    register,
    validate_policy,
)

GOOD_SOURCE = """
from flux import ExecutionContext, task, workflow


@task
async def double(x: int) -> int:
    return x * 2


@workflow
async def crunch(ctx: ExecutionContext[int]):
    return await double(ctx.input or 1)
"""


def _config(**overrides):
    base = {
        "enabled": True,
        "require_runner": "docker-airgapped",
        "max_source_bytes": 65536,
        "max_per_agent": 50,
        "ttl": 604800,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh on-disk SQLite so catalog writes are real."""
    db_path = tmp_path / "dynamic.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")
    yield
    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


class TestNamespaceDerivation:
    def test_deterministic_and_charset_safe(self):
        ns = namespace_for_subject("My Agent.01")
        assert ns == namespace_for_subject("My Agent.01")
        assert ns.startswith("dyn-")
        assert len(ns) <= 64
        validate_namespace_charset = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]*$")
        assert validate_namespace_charset.match(ns)

    def test_distinct_subjects_stay_distinct_even_when_slugs_collide(self):
        assert namespace_for_subject("agent-a") != namespace_for_subject("Agent.A")

    def test_long_subjects_fit_the_namespace_limit(self):
        assert len(namespace_for_subject("x" * 300)) <= 64


class TestReservedPrefix:
    def test_validate_namespace_rejects_dyn_prefix(self):
        with pytest.raises(ValueError, match="reserved"):
            validate_namespace("dyn-sneaky")

    def test_workflow_decorator_rejects_dyn_prefix(self):
        from flux import workflow

        with pytest.raises(ValueError, match="reserved"):

            @workflow.with_options(namespace="dyn-sneaky")
            async def squatter(ctx):
                pass


class TestReferenceResolution:
    def test_dyn_references_resolve(self):
        """The reservation binds at authoring; REFERRING to a dynamic
        workflow (call(), FluxClient, run_workflow ref=...) must work."""
        from flux.catalogs import resolve_workflow_ref

        ns = namespace_for_subject("agent-ref")
        assert resolve_workflow_ref(f"{ns}/crunch") == (ns, "crunch")


class TestPolicyValidation:
    def test_plain_workflow_accepted(self):
        node = validate_policy(GOOD_SOURCE.encode())
        assert node.name == "crunch"

    def test_allowed_with_options_accepted(self):
        source = """
from flux import ExecutionContext, workflow


@workflow.with_options(name="renamed", durability="transient")
async def wf(ctx: ExecutionContext):
    return 1
"""
        assert validate_policy(source.encode()).name == "wf"

    @pytest.mark.parametrize(
        "options",
        [
            'schedule=cron("* * * * *")',
            "requests=ResourceRequest(cpu=64)",
            'affinity={"gpu": "true"}',
            "routing=score(least(load()))",
            'namespace="default"',
            'runner="inprocess"',
            'service="public-endpoint"',
        ],
    )
    def test_platform_owned_options_rejected(self, options):
        source = f"""
from flux import ExecutionContext, workflow


@workflow.with_options({options})
async def wf(ctx: ExecutionContext):
    return 1
"""
        with pytest.raises(DynamicRegistrationError, match="not allowed"):
            validate_policy(source.encode())

    def test_kwargs_splat_rejected(self):
        """with_options(**opts) is dynamic — it could smuggle any option
        past the allowlist, so it is rejected outright."""
        source = """
from flux import ExecutionContext, workflow

opts = {"schedule": "sneaky"}


@workflow.with_options(**opts)
async def wf(ctx: ExecutionContext):
    return 1
"""
        with pytest.raises(DynamicRegistrationError, match=r"with_options\(\*\*"):
            validate_policy(source.encode())

    def test_zero_workflows_rejected(self):
        with pytest.raises(DynamicRegistrationError, match="exactly one"):
            validate_policy(b"x = 1")

    def test_two_workflows_rejected(self):
        source = """
from flux import ExecutionContext, workflow


@workflow
async def one(ctx: ExecutionContext):
    pass


@workflow
async def two(ctx: ExecutionContext):
    pass
"""
        with pytest.raises(DynamicRegistrationError, match="exactly one"):
            validate_policy(source.encode())

    def test_syntax_error_is_a_policy_error(self):
        with pytest.raises(DynamicRegistrationError, match="syntax error"):
            validate_policy(b"def broken(:\n  pass")


class TestRegistration:
    def test_registers_into_derived_namespace_with_stamped_runner(self, db):
        result = register(GOOD_SOURCE.encode(), subject="agent-a", config=_config())

        assert result["namespace"] == namespace_for_subject("agent-a")
        assert result["name"] == "crunch"
        assert result["version"] == 1
        assert result["existing"] is False

        from flux.catalogs import WorkflowCatalog

        entry = WorkflowCatalog.create().get(result["namespace"], "crunch")
        assert entry.metadata["runner"] == "docker-airgapped"
        dynamic = entry.metadata["dynamic"]
        assert dynamic["created_by"] == "agent-a"
        assert dynamic["source_hash"]
        assert dynamic["last_used_at"]

    def test_source_declared_runner_is_overridden_by_stamp(self, db):
        # runner= is rejected by policy, so the stamp can only be exercised
        # via config: a non-default require_runner lands in metadata.
        result = register(
            GOOD_SOURCE.encode(),
            subject="agent-r",
            config=_config(require_runner="docker"),
        )
        from flux.catalogs import WorkflowCatalog

        entry = WorkflowCatalog.create().get(result["namespace"], "crunch")
        assert entry.metadata["runner"] == "docker"

    def test_identical_source_is_idempotent(self, db):
        first = register(GOOD_SOURCE.encode(), subject="agent-b", config=_config())
        second = register(GOOD_SOURCE.encode(), subject="agent-b", config=_config())

        assert second["existing"] is True
        assert second["version"] == first["version"] == 1

    def test_changed_source_bumps_version(self, db):
        register(GOOD_SOURCE.encode(), subject="agent-c", config=_config())
        changed = GOOD_SOURCE.replace("x * 2", "x * 3")
        result = register(changed.encode(), subject="agent-c", config=_config())

        assert result["existing"] is False
        assert result["version"] == 2

    def test_quota_enforced_on_distinct_names(self, db):
        config = _config(max_per_agent=1)
        register(GOOD_SOURCE.encode(), subject="agent-q", config=config)

        other = GOOD_SOURCE.replace("async def crunch", "async def munch")
        with pytest.raises(DynamicRegistrationError, match="max_per_agent"):
            register(other.encode(), subject="agent-q", config=config)

        # Same-name updates still pass the quota (it counts distinct names).
        changed = GOOD_SOURCE.replace("x * 2", "x * 5")
        assert register(changed.encode(), subject="agent-q", config=config)["version"] == 2

    def test_size_cap(self, db):
        config = _config(max_source_bytes=64)
        with pytest.raises(DynamicRegistrationError, match="cap"):
            register(GOOD_SOURCE.encode(), subject="agent-s", config=config)

    def test_namespaces_are_isolated_per_subject(self, db):
        a = register(GOOD_SOURCE.encode(), subject="agent-x", config=_config())
        b = register(GOOD_SOURCE.encode(), subject="agent-y", config=_config())
        assert a["namespace"] != b["namespace"]


class TestGC:
    def _age(self, namespace: str, name: str, *, days: int):
        from flux.models import RepositoryFactory, WorkflowModel

        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            model = (
                session.query(WorkflowModel)
                .filter(WorkflowModel.namespace == namespace, WorkflowModel.name == name)
                .order_by(WorkflowModel.version.desc())
                .first()
            )
            metadata = dict(model.wf_metadata or {})
            dynamic = dict(metadata.get("dynamic") or {})
            dynamic["last_used_at"] = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()
            metadata["dynamic"] = dynamic
            model.wf_metadata = metadata
            session.commit()
            return model.id

    def test_stale_entry_collected_fresh_entry_kept(self, db):
        stale = register(GOOD_SOURCE.encode(), subject="agent-gc1", config=_config())
        fresh_src = GOOD_SOURCE.replace("async def crunch", "async def keepme")
        fresh = register(fresh_src.encode(), subject="agent-gc1", config=_config())
        self._age(stale["namespace"], "crunch", days=30)

        removed = gc_sweep(ttl_seconds=7 * 86400)

        assert removed == 1
        from flux.catalogs import WorkflowCatalog
        from flux.errors import WorkflowNotFoundError

        catalog = WorkflowCatalog.create()
        assert catalog.get(fresh["namespace"], "keepme") is not None
        with pytest.raises(WorkflowNotFoundError):
            catalog.get(stale["namespace"], "crunch")

    def test_live_execution_blocks_collection(self, db):
        result = register(GOOD_SOURCE.encode(), subject="agent-gc2", config=_config())
        workflow_row_id = self._age(result["namespace"], "crunch", days=30)

        from flux import ExecutionContext
        from flux.context_managers import ContextManager

        ContextManager.create().save(
            ExecutionContext(
                workflow_id=workflow_row_id,
                workflow_namespace=result["namespace"],
                workflow_name="crunch",
                execution_id="exec-live-gc",
            ),
        )

        assert gc_sweep(ttl_seconds=7 * 86400) == 0

    def test_run_refreshes_gc_clock(self, db):
        """Creating an execution of a dyn-* workflow refreshes last_used_at
        (the server's _create_execution choke point), so frequently run
        workflows are never collected just because nobody re-registers."""
        result = register(GOOD_SOURCE.encode(), subject="agent-gc4", config=_config())
        self._age(result["namespace"], "crunch", days=30)

        from flux.server import Server

        server = Server("127.0.0.1", 0)
        ctx = server._create_execution(result["namespace"], "crunch", None)
        assert ctx.execution_id

        assert gc_sweep(ttl_seconds=7 * 86400) == 0  # clock refreshed by the run

    def test_zero_ttl_disables(self, db):
        result = register(GOOD_SOURCE.encode(), subject="agent-gc3", config=_config())
        self._age(result["namespace"], "crunch", days=365)
        assert gc_sweep(ttl_seconds=0) == 0


class TestRunWorkflowArgs:
    @pytest.mark.asyncio
    async def test_requires_exactly_one_of_source_or_ref(self):
        # Call the unwrapped function: the task wrapper requires an active
        # execution context, but the argument contract is context-free.
        from flux.tasks.dynamic import run_workflow

        fn = run_workflow._func
        with pytest.raises(ValueError, match="exactly one"):
            await fn()
        with pytest.raises(ValueError, match="exactly one"):
            await fn(source="x", ref="a/b")
        with pytest.raises(ValueError, match="namespace/name"):
            await fn(ref="no-slash")
