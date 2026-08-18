"""Authorization for ``PUT /executions/{execution_id}/name``.

Renaming is a write on someone else's execution -- it is what the console's
session rail edits -- and carries the same permission as cancel:
``workflow:{ns}:{wf}:run``. tests/flux/test_execution_name.py covers the
route with auth disabled, so the permission check itself had no test
(#245); a regression that dropped it would have kept that suite green.

Harness copied from tests/security/test_hook_authz.py: only authentication
is stubbed, so these run against the real permission model.
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
    db_path = tmp_path / "rename_authz.db"
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


class _AuthProxy:
    """The server's real AuthService with only authentication stubbed."""

    def __init__(self, real, identity: FluxIdentity):
        self._real = real
        self._identity = identity

    async def authenticate(self, _token):
        return self._identity

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextmanager
def _auth_as(identity: FluxIdentity):
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
        with patch("flux.security.dependencies._get_auth_service", return_value=proxy):
            yield
    finally:
        settings.security.auth.enabled = original_enabled
        settings.security.auth.api_keys.enabled = original_api_keys


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


def _identity_holding(*permissions: str) -> FluxIdentity:
    from flux.models import RepositoryFactory
    from flux.security.models import RoleModel

    role = f"rename_test_{uuid.uuid4().hex[:6]}"
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(RoleModel(name=role, permissions=list(permissions)))
        session.commit()
    return FluxIdentity(subject=f"operator-{uuid.uuid4().hex[:6]}", roles=frozenset({role}))


def _rename(client_, execution_id: str, identity: FluxIdentity, name: str = "renamed"):
    with _auth_as(identity):
        return client_.put(
            f"/executions/{execution_id}/name",
            headers={"Authorization": "Bearer fake-token"},
            json={"name": name},
        )


def _stored_name(execution_id: str) -> str | None:
    from flux.context_managers import ContextManager

    return ContextManager.create().get_summary(execution_id).get("name")


def test_rename_is_denied_without_run_on_the_executions_workflow(client):
    _seed_execution("exec-denied", "default", "release")

    response = _rename(client, "exec-denied", _identity_holding())

    assert response.status_code == 403, response.text
    assert "workflow:default:release:run" in response.json()["detail"]
    # A denial must not be a partial write.
    assert _stored_name("exec-denied") is None


def test_rename_is_allowed_with_run_on_the_executions_workflow(client):
    """The mirror case: without it, a check that denied everything would
    satisfy the test above just as well."""
    _seed_execution("exec-allowed", "default", "release")

    response = _rename(
        client,
        "exec-allowed",
        _identity_holding("workflow:default:release:run"),
    )

    assert response.status_code == 200, response.text
    assert _stored_name("exec-allowed") == "renamed"


def test_the_permission_is_scoped_to_the_executions_own_workflow(client):
    """Run on another workflow is not run on this one."""
    _seed_execution("exec-other", "default", "release")

    response = _rename(
        client,
        "exec-other",
        _identity_holding("workflow:default:something_else:run"),
    )

    assert response.status_code == 403, response.text
    assert _stored_name("exec-other") is None
