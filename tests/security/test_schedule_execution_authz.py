"""Authorization tests for schedule creation and execution reads.

Two escalation/leak guards, both exercised with auth enabled:

1. ``schedule:*:manage`` alone must not let a caller schedule a workflow
   they cannot run themselves — at trigger time the workflow runs with the
   bound service account's roles, so an unchecked create/update is
   privilege escalation through the SA.
2. ``execution:*:read`` alone must not expose executions across workflow
   read boundaries — the detailed DTO carries workflow inputs/outputs, so
   reads are scoped like the approvals listing.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.security.identity import FluxIdentity


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A FluxServer app backed by a fresh on-disk SQLite database."""
    db_path = tmp_path / "sched_exec_authz.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api())

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


def _seed_role(name: str, permissions: list[str]) -> None:
    from flux.models import RepositoryFactory
    from flux.security.models import RoleModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(RoleModel(name=name, permissions=permissions))
        session.commit()


def _seed_workflow(namespace: str, name: str) -> str:
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    wf_id = f"{namespace}/{name}"
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=wf_id,
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace=namespace,
            ),
        )
        session.commit()
    return wf_id


def _seed_service_account(subject: str) -> None:
    from flux.models import RepositoryFactory
    from flux.security.principals import PrincipalRegistry

    repo = RepositoryFactory.create_repository()
    registry = PrincipalRegistry(session_factory=lambda: repo.session())
    registry.create(type="service_account", subject=subject, external_issuer="flux")


def _seed_execution(execution_id: str, namespace: str, workflow_name: str) -> None:
    from flux import ExecutionContext
    from flux.context_managers import ContextManager

    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"{namespace}/{workflow_name}",
        workflow_namespace=namespace,
        workflow_name=workflow_name,
        input=None,
        execution_id=execution_id,
    )
    ContextManager.create().save(ctx)


class _AuthProxy:
    """The server's real AuthService with only authentication stubbed:
    role -> permission resolution and authorize() stay real, so the tests
    exercise the actual permission model against DB-seeded roles."""

    def __init__(self, real, identity: FluxIdentity):
        self._real = real
        self._identity = identity

    async def authenticate(self, _token):
        return self._identity

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextmanager
def _auth_as(identity: FluxIdentity):
    """Enable auth with authentication mocked to yield ``identity``;
    authorization (role -> permission resolution) stays real."""
    from flux.config import Configuration
    from flux.security import dependencies

    real_service = dependencies._get_auth_service()
    assert real_service is not None, "server must have initialized the auth service"
    proxy = _AuthProxy(real_service, identity)

    settings = Configuration.get().settings
    original_enabled = settings.security.auth.enabled
    original_api_keys = settings.security.auth.api_keys.enabled
    settings.security.auth.enabled = True
    settings.security.auth.api_keys.enabled = True
    try:
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=proxy,
        ):
            yield
    finally:
        settings.security.auth.enabled = original_enabled
        settings.security.auth.api_keys.enabled = original_api_keys


def _headers():
    return {"Authorization": "Bearer fake-token"}


# ---------------------------------------------------------------------------
# Schedule creation: run_as_service_account escalation guard
# ---------------------------------------------------------------------------


def test_schedule_create_denies_caller_who_cannot_run_workflow(client):
    """schedule:*:manage without workflow run permission -> 403; no schedule
    row is created and the SA's privileges are never reachable."""
    suffix = uuid.uuid4().hex[:6]
    _seed_workflow("default", f"payout_{suffix}")
    _seed_service_account(f"admin-sa-{suffix}")
    _seed_role(f"sched_only_{suffix}", ["schedule:*:manage"])
    identity = FluxIdentity(subject="scheduler-user", roles=frozenset({f"sched_only_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/schedules",
            headers=_headers(),
            json={
                "workflow_name": f"payout_{suffix}",
                "workflow_namespace": "default",
                "name": f"nightly-{suffix}",
                "schedule_config": {"type": "interval", "interval_seconds": 3600},
                "run_as_service_account": f"admin-sa-{suffix}",
            },
        )
    assert resp.status_code == 403, resp.text

    listing = client.get("/schedules")
    names = [s["name"] for s in listing.json()]
    assert f"nightly-{suffix}" not in names


def test_schedule_create_allows_caller_who_can_run_workflow(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_workflow("default", f"deploy_{suffix}")
    _seed_service_account(f"deploy-sa-{suffix}")
    _seed_role(
        f"sched_run_{suffix}",
        [
            "schedule:*:manage",
            f"workflow:default:deploy_{suffix}:*",
            f"principal:deploy-sa-{suffix}:impersonate",
        ],
    )
    identity = FluxIdentity(subject="release-manager", roles=frozenset({f"sched_run_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/schedules",
            headers=_headers(),
            json={
                "workflow_name": f"deploy_{suffix}",
                "workflow_namespace": "default",
                "name": f"nightly-ok-{suffix}",
                "schedule_config": {"type": "interval", "interval_seconds": 3600},
                "run_as_service_account": f"deploy-sa-{suffix}",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run_as_service_account"] == f"deploy-sa-{suffix}"


def test_schedule_update_denies_sa_rebind_without_workflow_run(client):
    """Rebinding run_as_service_account on PUT is guarded like create."""
    from flux.domain.schedule import schedule_factory
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    wf_id = _seed_workflow("default", f"billing_{suffix}")
    _seed_service_account(f"sa-a-{suffix}")
    _seed_service_account(f"sa-b-{suffix}")

    # Seed the schedule directly (as an operator with full rights would).
    manager = create_schedule_manager()
    schedule_model = manager.create_schedule(
        workflow_id=wf_id,
        workflow_namespace="default",
        workflow_name=f"billing_{suffix}",
        name=f"weekly-{suffix}",
        schedule=schedule_factory({"type": "interval", "interval_seconds": 3600}),
        run_as_service_account=f"sa-a-{suffix}",
    )

    _seed_role(f"sched_only_upd_{suffix}", ["schedule:*:manage"])
    identity = FluxIdentity(subject="intruder", roles=frozenset({f"sched_only_upd_{suffix}"}))

    with _auth_as(identity):
        resp = client.put(
            f"/schedules/{schedule_model.id}",
            headers=_headers(),
            json={"run_as_service_account": f"sa-b-{suffix}"},
        )
    assert resp.status_code == 403, resp.text


def test_schedule_update_denies_retarget_without_workflow_run(client):
    """Escalation through PUT does not require touching run_as_service_account.
    Rewriting schedule_config/input_data alone re-aims the schedule's EXISTING
    privileged SA at attacker-chosen input on an attacker-chosen cadence, so
    the guard cannot be conditional on the SA being rebound."""
    from flux.domain.schedule import schedule_factory
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    wf_id = _seed_workflow("default", f"payroll_{suffix}")
    _seed_service_account(f"payroll-sa-{suffix}")

    manager = create_schedule_manager()
    schedule_model = manager.create_schedule(
        workflow_id=wf_id,
        workflow_namespace="default",
        workflow_name=f"payroll_{suffix}",
        name=f"payroll-auto-{suffix}",
        schedule=schedule_factory({"type": "interval", "interval_seconds": 3600}),
        run_as_service_account=f"payroll-sa-{suffix}",
    )

    before = {
        "input_data": schedule_model.input_data,
        "interval": schedule_model.schedule_config.interval,
        "next_run_at": schedule_model.next_run_at,
        "run_as_service_account": schedule_model.run_as_service_account,
    }

    _seed_role(f"sched_only_cfg_{suffix}", ["schedule:*:manage"])
    identity = FluxIdentity(subject="intruder", roles=frozenset({f"sched_only_cfg_{suffix}"}))

    with _auth_as(identity):
        resp = client.put(
            f"/schedules/{schedule_model.id}",
            headers=_headers(),
            json={
                "schedule_config": {"type": "interval", "interval_seconds": 10},
                "input_data": {"amount": 1000000, "account": "attacker"},
            },
        )
    assert resp.status_code == 403, resp.text

    # The stored schedule is untouched — not merely free of the payload.
    reloaded = manager.get_schedule(schedule_model.id)
    assert reloaded.input_data == before["input_data"]
    assert reloaded.schedule_config.interval == before["interval"]
    assert reloaded.next_run_at == before["next_run_at"]
    assert reloaded.run_as_service_account == before["run_as_service_account"]


def test_schedule_update_allows_retarget_for_caller_who_can_run_workflow(client):
    """The guard must not block a caller who is authorized to run the
    workflow — otherwise legitimate schedule edits break."""
    from flux.domain.schedule import schedule_factory
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    wf_id = _seed_workflow("default", f"report_{suffix}")

    manager = create_schedule_manager()
    schedule_model = manager.create_schedule(
        workflow_id=wf_id,
        workflow_namespace="default",
        workflow_name=f"report_{suffix}",
        name=f"report-auto-{suffix}",
        schedule=schedule_factory({"type": "interval", "interval_seconds": 3600}),
    )

    _seed_role(
        f"sched_run_upd_{suffix}",
        ["schedule:*:manage", f"workflow:default:report_{suffix}:*"],
    )
    identity = FluxIdentity(subject="owner", roles=frozenset({f"sched_run_upd_{suffix}"}))

    with _auth_as(identity):
        resp = client.put(
            f"/schedules/{schedule_model.id}",
            headers=_headers(),
            json={"schedule_config": {"type": "interval", "interval_seconds": 60}},
        )
    assert resp.status_code == 200, resp.text


def test_auth_permissions_lists_only_readable_workflows(client):
    """`/auth/permissions` enumerates namespaces, workflow names and each
    workflow's task names. Its siblings (`/workflows`, `/namespaces`) filter
    that inventory on workflow read; this route must too."""
    suffix = uuid.uuid4().hex[:6]
    _seed_workflow("default", f"visible_{suffix}")
    _seed_workflow("default", f"hidden_{suffix}")

    _seed_role(f"one_wf_{suffix}", [f"workflow:default:visible_{suffix}:read"])
    identity = FluxIdentity(subject="tenant", roles=frozenset({f"one_wf_{suffix}"}))

    with _auth_as(identity):
        resp = client.get("/auth/permissions", headers=_headers())
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert f"default/visible_{suffix}" in listed
    assert f"default/hidden_{suffix}" not in listed


def test_auth_permissions_single_workflow_requires_read(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_workflow("default", f"secret_{suffix}")

    _seed_role(f"no_wf_{suffix}", ["execution:*:read"])
    identity = FluxIdentity(subject="tenant", roles=frozenset({f"no_wf_{suffix}"}))

    with _auth_as(identity):
        resp = client.get(
            "/auth/permissions",
            params={"workflow": f"default/secret_{suffix}"},
            headers=_headers(),
        )
    assert resp.status_code == 403, resp.text


def _seed_schedule(suffix: str, workflow_name: str):
    from flux.domain.schedule import schedule_factory
    from flux.schedule_manager import create_schedule_manager

    wf_id = _seed_workflow("default", workflow_name)
    return create_schedule_manager().create_schedule(
        workflow_id=wf_id,
        workflow_namespace="default",
        workflow_name=workflow_name,
        name=f"hist-{suffix}",
        schedule=schedule_factory({"type": "interval", "interval_seconds": 3600}),
    )


def test_schedule_resume_denies_caller_who_cannot_run_workflow(client):
    """Resuming makes the workflow fire under the bound service account on its
    configured cadence. Blocking the rewrite but not the resume would leave the
    escalation reachable — an operator's deliberate pause is the only thing
    standing between the attacker and the privileged run."""
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    schedule_model = _seed_schedule(suffix, f"payout_{suffix}")
    manager = create_schedule_manager()
    manager.pause_schedule(schedule_model.id)

    _seed_role(f"sched_resume_{suffix}", ["schedule:*:manage"])
    identity = FluxIdentity(subject="intruder", roles=frozenset({f"sched_resume_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(f"/schedules/{schedule_model.id}/resume", headers=_headers())
    assert resp.status_code == 403, resp.text


def test_schedule_resume_allows_caller_who_can_run_workflow(client):
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    schedule_model = _seed_schedule(suffix, f"batch_{suffix}")
    manager = create_schedule_manager()
    manager.pause_schedule(schedule_model.id)

    _seed_role(
        f"sched_resume_ok_{suffix}",
        ["schedule:*:manage", f"workflow:default:batch_{suffix}:*"],
    )
    identity = FluxIdentity(subject="owner", roles=frozenset({f"sched_resume_ok_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(f"/schedules/{schedule_model.id}/resume", headers=_headers())
    assert resp.status_code == 200, resp.text


def test_schedule_pause_denies_caller_without_workflow_read(client):
    """Pausing only stops execution, so it takes the lighter read boundary that
    get_schedule uses rather than full run authorization."""
    suffix = uuid.uuid4().hex[:6]
    schedule_model = _seed_schedule(suffix, f"nightly_{suffix}")

    _seed_role(f"sched_pause_{suffix}", ["schedule:*:manage"])
    identity = FluxIdentity(subject="intruder", roles=frozenset({f"sched_pause_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(f"/schedules/{schedule_model.id}/pause", headers=_headers())
    assert resp.status_code == 403, resp.text


def test_schedule_history_denies_reader_without_workflow_read(client):
    """`get_schedule` scopes to the bound workflow; history exposes the same
    workflow's execution ids, states and error strings, so it must scope too."""
    suffix = uuid.uuid4().hex[:6]
    schedule_model = _seed_schedule(suffix, f"finance_{suffix}")

    _seed_role(f"sched_read_{suffix}", ["schedule:*:read"])
    identity = FluxIdentity(subject="nosy", roles=frozenset({f"sched_read_{suffix}"}))

    with _auth_as(identity):
        resp = client.get(f"/schedules/{schedule_model.id}/history", headers=_headers())
    assert resp.status_code == 403, resp.text


def test_schedule_history_allows_reader_with_workflow_read(client):
    suffix = uuid.uuid4().hex[:6]
    schedule_model = _seed_schedule(suffix, f"ledger_{suffix}")

    _seed_role(
        f"sched_read_ok_{suffix}",
        ["schedule:*:read", f"workflow:default:ledger_{suffix}:read"],
    )
    identity = FluxIdentity(subject="owner", roles=frozenset({f"sched_read_ok_{suffix}"}))

    with _auth_as(identity):
        resp = client.get(f"/schedules/{schedule_model.id}/history", headers=_headers())
    assert resp.status_code == 200, resp.text


def test_schedule_update_sa_rebind_fails_closed_when_workflow_unregistered(client):
    """If the schedule's workflow is gone from the catalog, the rebind guard
    cannot derive task-level permission requirements — it must refuse rather
    than authorize against empty metadata."""
    from flux.domain.schedule import schedule_factory
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    _seed_service_account(f"sa-x-{suffix}")

    # Schedule references a workflow that was never registered (SQLite does
    # not enforce the schedules -> workflows FK, mirroring a post-deletion row).
    manager = create_schedule_manager()
    schedule_model = manager.create_schedule(
        workflow_id=f"default/ghost_{suffix}",
        workflow_namespace="default",
        workflow_name=f"ghost_{suffix}",
        name=f"ghost-sched-{suffix}",
        schedule=schedule_factory({"type": "interval", "interval_seconds": 3600}),
        run_as_service_account=f"sa-x-{suffix}",
    )

    # Impersonation is granted so the request reaches the catalog check this
    # test is about, rather than stopping at the earlier 403.
    _seed_role(
        f"full_{suffix}",
        ["schedule:*:manage", "workflow:*:*:*", f"principal:sa-x-{suffix}:impersonate"],
    )
    identity = FluxIdentity(subject="operator", roles=frozenset({f"full_{suffix}"}))

    with _auth_as(identity):
        resp = client.put(
            f"/schedules/{schedule_model.id}",
            headers=_headers(),
            json={"run_as_service_account": f"sa-x-{suffix}"},
        )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Execution reads: workflow-scoped like the approvals listing
# ---------------------------------------------------------------------------


def _scoped_reader(suffix: str) -> FluxIdentity:
    _seed_role(
        f"ns1_reader_{suffix}",
        ["execution:*:read", f"workflow:ns1_{suffix}:*:read"],
    )
    return FluxIdentity(subject="ns1-analyst", roles=frozenset({f"ns1_reader_{suffix}"}))


def test_execution_get_denies_cross_workflow_reader(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_execution(f"exec-ns1-{suffix}", f"ns1_{suffix}", "wf_one")
    _seed_execution(f"exec-ns2-{suffix}", f"ns2_{suffix}", "wf_two")
    identity = _scoped_reader(suffix)

    with _auth_as(identity):
        # Default params take the summary fast path; detailed=true takes the
        # fully-hydrated path. Both must enforce the workflow-read boundary.
        allowed = client.get(f"/executions/exec-ns1-{suffix}", headers=_headers())
        denied = client.get(f"/executions/exec-ns2-{suffix}", headers=_headers())
        denied_detailed = client.get(
            f"/executions/exec-ns2-{suffix}",
            params={"detailed": True},
            headers=_headers(),
        )

    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 403, denied.text
    assert denied_detailed.status_code == 403, denied_detailed.text


def test_executions_list_scopes_to_readable_workflows(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_execution(f"exec-l1-{suffix}", f"ns1_{suffix}", "wf_one")
    _seed_execution(f"exec-l2-{suffix}", f"ns2_{suffix}", "wf_two")
    identity = _scoped_reader(suffix)

    with _auth_as(identity):
        resp = client.get("/executions", headers=_headers())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auth_filtered"] is True
    namespaces = {e["workflow_namespace"] for e in body["executions"]}
    assert namespaces == {f"ns1_{suffix}"}
    assert body["total"] == 1


def test_executions_list_broad_reader_unfiltered(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_execution(f"exec-b1-{suffix}", f"ns1_{suffix}", "wf_one")
    _seed_execution(f"exec-b2-{suffix}", f"ns2_{suffix}", "wf_two")
    _seed_role(f"broad_{suffix}", ["execution:*:read", "workflow:*:*:read"])
    identity = FluxIdentity(subject="sre", roles=frozenset({f"broad_{suffix}"}))

    with _auth_as(identity):
        resp = client.get("/executions", headers=_headers())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auth_filtered"] is False
    namespaces = {e["workflow_namespace"] for e in body["executions"]}
    assert {f"ns1_{suffix}", f"ns2_{suffix}"} <= namespaces


def test_executions_list_no_workflow_read_at_all_is_403(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_execution(f"exec-n1-{suffix}", f"ns1_{suffix}", "wf_one")
    _seed_role(f"exec_only_{suffix}", ["execution:*:read"])
    identity = FluxIdentity(subject="nobody", roles=frozenset({f"exec_only_{suffix}"}))

    with _auth_as(identity):
        resp = client.get("/executions", headers=_headers())

    assert resp.status_code == 403, resp.text


def test_schedule_create_denies_binding_a_service_account_without_impersonate(client):
    """schedule:*:manage plus the right to run the workflow is not enough: the
    schedule runs under the SA's roles, so binding one is impersonation."""
    suffix = uuid.uuid4().hex[:6]
    _seed_workflow("default", f"batch_{suffix}")
    _seed_service_account(f"privileged-sa-{suffix}")
    _seed_role(
        f"no_imp_{suffix}",
        ["schedule:*:manage", f"workflow:default:batch_{suffix}:*"],
    )
    identity = FluxIdentity(subject="dev", roles=frozenset({f"no_imp_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/schedules",
            headers=_headers(),
            json={
                "workflow_name": f"batch_{suffix}",
                "workflow_namespace": "default",
                "name": f"nightly-imp-{suffix}",
                "schedule_config": {"type": "interval", "interval_seconds": 3600},
                "run_as_service_account": f"privileged-sa-{suffix}",
            },
        )
    assert resp.status_code == 403, resp.text


def test_impersonate_grant_is_per_subject(client):
    """A grant for one service account must not authorize another."""
    suffix = uuid.uuid4().hex[:6]
    _seed_workflow("default", f"job_{suffix}")
    _seed_service_account(f"allowed-sa-{suffix}")
    _seed_service_account(f"other-sa-{suffix}")
    _seed_role(
        f"one_sa_{suffix}",
        [
            "schedule:*:manage",
            f"workflow:default:job_{suffix}:*",
            f"principal:allowed-sa-{suffix}:impersonate",
        ],
    )
    identity = FluxIdentity(subject="dev", roles=frozenset({f"one_sa_{suffix}"}))

    def _create(sa: str, name: str):
        return client.post(
            "/schedules",
            headers=_headers(),
            json={
                "workflow_name": f"job_{suffix}",
                "workflow_namespace": "default",
                "name": name,
                "schedule_config": {"type": "interval", "interval_seconds": 3600},
                "run_as_service_account": sa,
            },
        )

    with _auth_as(identity):
        assert _create(f"allowed-sa-{suffix}", f"ok-{suffix}").status_code == 200
        assert _create(f"other-sa-{suffix}", f"nope-{suffix}").status_code == 403


def test_schedule_update_rebind_requires_impersonate(client):
    from flux.domain.schedule import schedule_factory
    from flux.schedule_manager import create_schedule_manager

    suffix = uuid.uuid4().hex[:6]
    wf_id = _seed_workflow("default", f"rebind_{suffix}")
    _seed_service_account(f"target-sa-{suffix}")

    schedule_model = create_schedule_manager().create_schedule(
        workflow_id=wf_id,
        workflow_namespace="default",
        workflow_name=f"rebind_{suffix}",
        name=f"rebind-sched-{suffix}",
        schedule=schedule_factory({"type": "interval", "interval_seconds": 3600}),
    )

    _seed_role(
        f"rebind_{suffix}",
        ["schedule:*:manage", f"workflow:default:rebind_{suffix}:*"],
    )
    identity = FluxIdentity(subject="dev", roles=frozenset({f"rebind_{suffix}"}))

    with _auth_as(identity):
        resp = client.put(
            f"/schedules/{schedule_model.id}",
            headers=_headers(),
            json={"run_as_service_account": f"target-sa-{suffix}"},
        )
    assert resp.status_code == 403, resp.text
