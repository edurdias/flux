"""Authorization for POST /workflows/dynamic (agent-only by construction).

With auth enabled the endpoint accepts nothing but execution tokens, and the
per-principal namespace is derived server-side from the token subject — so
the permission matrix and the no-API-key rule are the security boundary of
the dynamic-workflows feature (execution containment is PR 1's runner).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.dynamic_workflows import namespace_for_subject
from flux.security.identity import FluxIdentity

GOOD_SOURCE = """
from flux import ExecutionContext, workflow


@workflow
async def authored(ctx: ExecutionContext):
    return "ok"
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "dynamic_authz.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")
    Configuration.get().settings.dynamic_workflows.enabled = True

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


class _AuthProxy:
    """Real AuthService with only authentication stubbed (role resolution
    stays real, against DB-seeded roles)."""

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
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=proxy,
        ):
            yield
    finally:
        settings.security.auth.enabled = original_enabled
        settings.security.auth.api_keys.enabled = original_api_keys


def _post(client, source=GOOD_SOURCE):
    return client.post(
        "/workflows/dynamic",
        json={"source": source},
        headers={"Authorization": "Bearer fake-token"},
    )


def _execution_identity(subject: str, roles: frozenset[str]) -> FluxIdentity:
    return FluxIdentity(
        subject=subject,
        roles=roles,
        metadata={"token_type": "execution", "exec_id": "exec-1"},
    )


def test_disabled_feature_is_404(client):
    from flux.config import Configuration

    Configuration.get().settings.dynamic_workflows.enabled = False
    resp = client.post("/workflows/dynamic", json={"source": GOOD_SOURCE})
    assert resp.status_code == 404


def test_non_execution_token_rejected(client):
    suffix = uuid.uuid4().hex[:6]
    subject = f"human-{suffix}"
    _seed_role(f"dyn_{suffix}", [f"workflow:{namespace_for_subject(subject)}:*:register"])
    # Same grants, but an API-key identity — must be rejected on token type.
    identity = FluxIdentity(
        subject=subject,
        roles=frozenset({f"dyn_{suffix}"}),
        metadata={"token_type": "api_key"},
    )
    with _auth_as(identity):
        resp = _post(client)
    assert resp.status_code == 403, resp.text
    assert "execution tokens" in resp.text


def test_execution_token_without_grant_rejected(client):
    identity = _execution_identity("agent-nogrant", frozenset())
    with _auth_as(identity):
        resp = _post(client)
    assert resp.status_code == 403, resp.text
    assert "register" in resp.text


def test_execution_token_with_grant_registers_into_derived_namespace(client):
    suffix = uuid.uuid4().hex[:6]
    subject = f"agent-{suffix}"
    namespace = namespace_for_subject(subject)
    _seed_role(f"dyn_ok_{suffix}", [f"workflow:{namespace}:*:register"])
    identity = _execution_identity(subject, frozenset({f"dyn_ok_{suffix}"}))

    with _auth_as(identity):
        resp = _post(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["namespace"] == namespace
    assert body["name"] == "authored"


def test_grant_for_own_namespace_does_not_open_others(client):
    """The namespace is derived from the SUBJECT, so even a broad grant on
    another agent's namespace cannot be used to write there — the derived
    namespace is the only one this identity can ever hit."""
    suffix = uuid.uuid4().hex[:6]
    subject = f"agent-cross-{suffix}"
    other_namespace = namespace_for_subject("someone-else")
    _seed_role(f"dyn_cross_{suffix}", [f"workflow:{other_namespace}:*:register"])
    identity = _execution_identity(subject, frozenset({f"dyn_cross_{suffix}"}))

    with _auth_as(identity):
        resp = _post(client)

    # Grant is for the other namespace; the derived one is unauthorized.
    assert resp.status_code == 403, resp.text


def test_policy_rejection_is_structured_422(client):
    bad = GOOD_SOURCE.replace(
        "@workflow",
        '@workflow.with_options(schedule="nope")',
    )
    resp = client.post("/workflows/dynamic", json={"source": bad})
    assert resp.status_code == 422, resp.text
    assert "not allowed" in resp.text


def test_auth_disabled_dev_path_registers(client):
    resp = client.post("/workflows/dynamic", json={"source": GOOD_SOURCE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["namespace"] == namespace_for_subject("anonymous")
