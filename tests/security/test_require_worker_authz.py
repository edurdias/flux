"""Authorization for X-Flux-Require-Worker (issue #187).

Binding an execution to a named node is not the same capability as running
the workflow: it concentrates load on one node and compels that node to
execute the code. So the binding header needs a worker-scoped grant on top of
the run permission, while the advisory X-Flux-Preferred-Worker stays ungated.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.security.identity import FluxIdentity

SOURCE = b"""
from flux import workflow


@workflow
async def bind_probe(ctx):
    return "ok"
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "require_worker_authz.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")
    # Auth is enabled below, so a successful run mints an execution token.
    monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "t" * 32)

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    test_client = TestClient(server._create_api())
    files = {"file": ("flow.py", SOURCE, "text/x-python")}
    assert test_client.post("/workflows", files=files).status_code == 200

    yield test_client

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


class _AuthProxy:
    """Real AuthService with only authentication stubbed, so role ->
    permission resolution is exercised for real."""

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
    assert real_service is not None
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


def _run(client, headers_extra):
    return client.post(
        "/workflows/default/bind_probe/run/async",
        json=None,
        headers={"Authorization": "Bearer fake-token", **headers_extra},
    )


def _identity_with(permissions: list[str]) -> FluxIdentity:
    role = f"r_{uuid.uuid4().hex[:8]}"
    _seed_role(role, permissions)
    return FluxIdentity(subject="caller", roles=frozenset({role}))


RUN_PERMS = ["workflow:*:*:run", "workflow:*:*:read"]


def test_run_permission_alone_cannot_bind(client):
    identity = _identity_with(RUN_PERMS)

    with _auth_as(identity):
        resp = _run(client, {"X-Flux-Require-Worker": "w1"})

    assert resp.status_code == 403
    assert "worker:w1:target" in resp.text


def test_worker_scoped_grant_allows_binding(client):
    identity = _identity_with([*RUN_PERMS, "worker:w1:target"])

    with _auth_as(identity):
        resp = _run(client, {"X-Flux-Require-Worker": "w1"})

    assert resp.status_code == 200, resp.text


def test_grant_is_scoped_to_the_named_worker(client):
    """A grant for one worker must not authorize binding to another."""
    identity = _identity_with([*RUN_PERMS, "worker:w1:target"])

    with _auth_as(identity):
        resp = _run(client, {"X-Flux-Require-Worker": "w2"})

    assert resp.status_code == 403
    assert "worker:w2:target" in resp.text


def test_worker_wildcard_satisfies_the_binding(client):
    """The built-in worker role holds worker:*:*, whose terminal wildcard
    covers the narrower target permission."""
    identity = _identity_with([*RUN_PERMS, "worker:*:*"])

    with _auth_as(identity):
        resp = _run(client, {"X-Flux-Require-Worker": "anything"})

    assert resp.status_code == 200, resp.text


def test_advisory_hint_stays_ungated(client):
    """The pre-existing hint is unaffected: it cannot force placement, so it
    never needed a worker grant and must not start needing one."""
    identity = _identity_with(RUN_PERMS)

    with _auth_as(identity):
        resp = _run(client, {"X-Flux-Preferred-Worker": "w1"})

    assert resp.status_code == 200, resp.text
