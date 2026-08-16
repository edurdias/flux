"""Registration-time wiring for workflow-declared hooks (declaration path
2): escalation + impersonation + runnable-target checks before any row is
written, owner-scoped reconciliation on save, and a real delete-with-
workflow on workflow removal. Auth is off here, matching
tests/flux/test_hook_routes.py; permission-string enforcement for the
escalation (`hook:*:create`) and impersonation (`principal:*:impersonate`)
gates is exercised with auth enabled in
tests/security/test_workflow_hook_authz.py, in the same style as
tests/security/test_hook_authz.py."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration


@pytest.fixture
def db(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'wf_hooks.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


@pytest.fixture
def server_instance(db):
    from flux.server import Server

    return Server(host="localhost", port=8000)


@pytest.fixture
def client(server_instance):
    return TestClient(server_instance._create_api())


def _seed_workflow(namespace: str, name: str) -> str:
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    workflow_id = f"{namespace}/{name}"
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=workflow_id,
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace=namespace,
            ),
        )
        session.commit()
    return workflow_id


def _seed_principal():
    from flux.security.principals import PrincipalRegistry
    from flux.models import RepositoryFactory

    repo = RepositoryFactory.create_repository()
    registry = PrincipalRegistry(session_factory=lambda: repo.session())
    return registry.create(type="service_account", subject="notifier", external_issuer="flux")


def _upload(client, source: str, filename: str = "wf.py"):
    return client.post(
        "/workflows",
        files={"file": (filename, io.BytesIO(source.encode()), "text/x-python")},
    )


_SOURCE = """
from flux import workflow
from flux.hooks import hook


@workflow.with_options(
    namespace="release",
    hooks=[
        hook.run(
            on="execution:release:notify_on_fail:failed",
            workflow="ops/notify",
            principal="notifier",
        ),
    ],
)
async def notify_on_fail(ctx):
    return ctx.input
"""


class TestWorkflowDeclaredHookRegistration:
    def test_registering_creates_the_declared_hook(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()

        resp = _upload(client, _SOURCE)

        assert resp.status_code == 200, resp.text
        listed = client.get("/hooks").json()
        owned = [h for h in listed["hooks"] if h["owner_type"] == "workflow"]
        assert len(owned) == 1
        assert owned[0]["owner_ref"] == "release/notify_on_fail"
        assert owned[0]["selectors"] == ["execution:release:notify_on_fail:failed"]
        assert owned[0]["principal"] == "notifier"

    def test_registering_without_a_runnable_target_is_rejected(self, client):
        _seed_principal()
        # "ops/notify" is never seeded, so the principal cannot run it.

        resp = _upload(client, _SOURCE)

        assert resp.status_code in (400, 403, 404), resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_reregistering_with_a_changed_selector_updates_the_same_row(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)
        first_id = client.get("/hooks").json()["hooks"][0]["id"]

        changed = _SOURCE.replace(
            "execution:release:notify_on_fail:failed",
            "execution:release:notify_on_fail:completed",
        )
        resp = _upload(client, changed)

        assert resp.status_code == 200, resp.text
        hooks = client.get("/hooks").json()["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["id"] == first_id
        assert hooks[0]["selectors"] == ["execution:release:notify_on_fail:completed"]

    def test_reregistering_without_the_hooks_kwarg_removes_it(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)
        assert len(client.get("/hooks").json()["hooks"]) == 1

        without_hooks = """
from flux import workflow


@workflow.with_options(namespace="release", name="notify_on_fail")
async def notify_on_fail(ctx):
    return ctx.input
"""
        resp = _upload(client, without_hooks)

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_deleting_the_workflows_only_version_deletes_its_owned_hooks(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)
        assert len(client.get("/hooks").json()["hooks"]) == 1

        resp = client.delete("/workflows/release/notify_on_fail")

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_deleting_one_of_several_versions_keeps_owned_hooks(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)  # v1
        _upload(client, _SOURCE)  # v2, same declaration
        hooks_before = client.get("/hooks").json()["hooks"]
        assert len(hooks_before) == 1

        resp = client.delete("/workflows/release/notify_on_fail?version=1")

        assert resp.status_code == 200, resp.text
        assert len(client.get("/hooks").json()["hooks"]) == 1
