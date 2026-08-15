"""Authorization tests for the hooks REST surface.

Two things are pinned down here, both with auth enabled and the real
role -> permission resolution:

1. The built-in roles per the spec — ``operator`` manages hooks, ``viewer``
   reads hooks and deliveries and nothing else, ``worker`` gets none of it.
2. The create-time half of fire-time authorization: a hook's stored
   ``principal_id`` must hold ``workflow:<ns>:<wf>:run`` on its target before
   the row is written, so a hook that could only ever dead-letter is refused
   at the door rather than at 3am.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.security.identity import FluxIdentity


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flux server app backed by a fresh on-disk SQLite database."""
    db_path = tmp_path / "hook_authz.db"
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
    """The server's real AuthService with only authentication stubbed, so the
    tests exercise the actual permission model."""

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


def _as(role: str) -> FluxIdentity:
    return FluxIdentity(subject=f"{role}-{uuid.uuid4().hex[:6]}", roles=frozenset({role}))


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


def _seed_principal(role: str) -> str:
    """A service-account principal holding ``role``; returns its id."""
    from flux.models import RepositoryFactory
    from flux.security.principals import PrincipalRegistry

    repo = RepositoryFactory.create_repository()
    registry = PrincipalRegistry(session_factory=lambda: repo.session())
    principal = registry.create(
        type="service_account",
        subject=f"hook-sa-{uuid.uuid4().hex[:8]}",
        external_issuer="flux",
    )
    registry.assign_role(principal.id, role)
    return principal.id


def _seed_hook(name: str, principal_id: str, workflow_ref: str = "ops/incident"):
    from flux.hooks.registry import HookRegistry

    return HookRegistry.create().create_hook(
        name=name,
        selectors=["execution:*:*:failed"],
        workflow_ref=workflow_ref,
        principal_id=principal_id,
        owner_ref="seed",
    )


def _seed_dead_delivery(hook_id: str) -> str:
    from flux.models import HookDeliveryModel, RepositoryFactory

    with RepositoryFactory.create_repository().session() as session:
        delivery = HookDeliveryModel(
            hook_id=hook_id,
            event_key=f"ev-{uuid.uuid4().hex[:8]}",
            payload={},
            status="dead",
            attempts=5,
            created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
        )
        session.add(delivery)
        session.commit()
        session.refresh(delivery)
        return delivery.id


@pytest.fixture
def hook(client):
    """A hook whose principal may run its (registered) target."""
    _seed_workflow("ops", "incident")
    principal_id = _seed_principal("operator")
    name = f"hook-{uuid.uuid4().hex[:6]}"
    return _seed_hook(name, principal_id)


def _create_body(name: str, principal_id: str) -> dict:
    return {
        "name": name,
        "selectors": ["execution:*:*:failed"],
        "workflow_ref": "ops/incident",
        "principal_id": principal_id,
    }


class TestViewer:
    def test_viewer_reads_hooks_and_deliveries(self, client, hook):
        _seed_dead_delivery(hook.id)

        with _auth_as(_as("viewer")):
            listed = client.get("/hooks", headers=_headers())
            got = client.get(f"/hooks/{hook.name}", headers=_headers())
            deliveries = client.get(f"/hooks/{hook.name}/deliveries", headers=_headers())

        assert listed.status_code == 200, listed.text
        assert got.status_code == 200, got.text
        assert deliveries.status_code == 200, deliveries.text
        assert len(deliveries.json()) == 1

    def test_viewer_cannot_write(self, client, hook):
        delivery_id = _seed_dead_delivery(hook.id)

        with _auth_as(_as("viewer")):
            created = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("new-hook", hook.principal_id),
            )
            updated = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"enabled": False},
            )
            tested = client.post(f"/hooks/{hook.name}/test", headers=_headers())
            retried = client.post(
                f"/hooks/{hook.name}/deliveries/{delivery_id}/retry",
                headers=_headers(),
            )
            deleted = client.delete(f"/hooks/{hook.name}", headers=_headers())

        assert created.status_code == 403, created.text
        assert updated.status_code == 403, updated.text
        assert tested.status_code == 403, tested.text
        assert retried.status_code == 403, retried.text
        assert deleted.status_code == 403, deleted.text


class TestWorker:
    def test_worker_gets_none_of_it(self, client, hook):
        with _auth_as(_as("worker")):
            listed = client.get("/hooks", headers=_headers())
            got = client.get(f"/hooks/{hook.name}", headers=_headers())
            deliveries = client.get(f"/hooks/{hook.name}/deliveries", headers=_headers())
            created = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("worker-hook", hook.principal_id),
            )

        assert listed.status_code == 403, listed.text
        assert got.status_code == 403, got.text
        assert deliveries.status_code == 403, deliveries.text
        assert created.status_code == 403, created.text


class TestOperator:
    def test_operator_manages_hooks_end_to_end(self, client, hook):
        name = f"ops-hook-{uuid.uuid4().hex[:6]}"
        delivery_id = _seed_dead_delivery(hook.id)

        with _auth_as(_as("operator")):
            created = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body(name, hook.principal_id),
            )
            updated = client.put(f"/hooks/{name}", headers=_headers(), json={"max_attempts": 2})
            tested = client.post(f"/hooks/{name}/test", headers=_headers())
            deliveries = client.get(f"/hooks/{hook.name}/deliveries", headers=_headers())
            retried = client.post(
                f"/hooks/{hook.name}/deliveries/{delivery_id}/retry",
                headers=_headers(),
            )
            deleted = client.delete(f"/hooks/{name}", headers=_headers())

        assert created.status_code == 200, created.text
        assert updated.status_code == 200, updated.text
        assert tested.status_code == 200, tested.text
        assert tested.json()["execution_id"]
        assert deliveries.status_code == 200, deliveries.text
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "pending"
        assert deleted.status_code == 200, deleted.text


class TestHookPrincipalMustRunTheTarget:
    def test_create_denies_a_principal_that_cannot_run_the_target(self, client):
        _seed_workflow("ops", "incident")
        powerless = _seed_principal("viewer")

        with _auth_as(_as("operator")):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("doomed", powerless),
            )
            listed = client.get("/hooks", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert "workflow:ops:incident:run" in resp.text
        assert listed.json()["total"] == 0

    def test_update_denies_rebinding_to_a_principal_that_cannot_run(self, client, hook):
        powerless = _seed_principal("viewer")

        with _auth_as(_as("operator")):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"principal_id": powerless},
            )
            got = client.get(f"/hooks/{hook.name}", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert got.json()["principal_id"] == hook.principal_id

    def test_test_fire_denies_a_principal_revoked_after_creation(self, client, hook):
        """The test fire starts a real execution as the hook's principal, so a
        principal disabled since the hook was created must not get one more
        run out of it."""
        from flux.models import RepositoryFactory
        from flux.security.principals import PrincipalRegistry

        repo = RepositoryFactory.create_repository()
        PrincipalRegistry(session_factory=lambda: repo.session()).set_enabled(
            hook.principal_id,
            False,
        )

        with _auth_as(_as("operator")):
            resp = client.post(f"/hooks/{hook.name}/test", headers=_headers())

        assert resp.status_code == 403, resp.text

    def test_create_denies_a_disabled_principal(self, client):
        from flux.models import RepositoryFactory
        from flux.security.principals import PrincipalRegistry

        _seed_workflow("ops", "incident")
        principal_id = _seed_principal("operator")
        repo = RepositoryFactory.create_repository()
        PrincipalRegistry(session_factory=lambda: repo.session()).set_enabled(principal_id, False)

        with _auth_as(_as("operator")):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("disabled-principal", principal_id),
            )

        assert resp.status_code == 403, resp.text
