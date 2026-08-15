"""Authorization tests for the hooks REST surface.

Two things are pinned down here, both with auth enabled and the real
role -> permission resolution:

1. The built-in roles per the spec — ``operator`` manages hooks, ``viewer``
   reads hooks and deliveries and nothing else, ``worker`` gets none of it.
2. The create-time half of fire-time authorization: a hook's stored
   ``principal`` must hold ``workflow:<ns>:<wf>:run`` on its target before
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


def _seed_principal(role: str):
    """A service-account principal holding ``role``; returns the row."""
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
    return principal


def _seed_role(name: str, permissions: list[str]) -> str:
    from flux.models import RepositoryFactory
    from flux.security.models import RoleModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(RoleModel(name=name, permissions=permissions))
        session.commit()
    return name


def _may_impersonate(subject: str) -> FluxIdentity:
    """An operator who also holds the grant to bind ``subject``."""
    role = _seed_role(
        f"hook_imp_{uuid.uuid4().hex[:6]}",
        [f"principal:{subject}:impersonate"],
    )
    return FluxIdentity(
        subject=f"operator-{uuid.uuid4().hex[:6]}",
        roles=frozenset({"operator", role}),
    )


def _seed_hook(name: str, principal: str, workflow_ref: str = "ops/incident"):
    from flux.hooks.registry import HookRegistry

    return HookRegistry.create().create_hook(
        name=name,
        selectors=["execution:*:*:failed"],
        workflow_ref=workflow_ref,
        principal=principal,
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
def hook_principal(client):
    """The principal a seeded hook runs its (registered) target as."""
    _seed_workflow("ops", "incident")
    return _seed_principal("operator")


@pytest.fixture
def hook(client, hook_principal):
    """A hook whose principal may run its (registered) target."""
    name = f"hook-{uuid.uuid4().hex[:6]}"
    return _seed_hook(name, hook_principal.subject)


def _create_body(name: str, principal: str) -> dict:
    return {
        "name": name,
        "selectors": ["execution:*:*:failed"],
        "workflow_ref": "ops/incident",
        "principal": principal,
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
                json=_create_body("new-hook", hook.principal),
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
                json=_create_body("worker-hook", hook.principal),
            )

        assert listed.status_code == 403, listed.text
        assert got.status_code == 403, got.text
        assert deliveries.status_code == 403, deliveries.text
        assert created.status_code == 403, created.text


class TestOperator:
    def test_operator_manages_hooks_end_to_end(self, client, hook, hook_principal):
        name = f"ops-hook-{uuid.uuid4().hex[:6]}"
        delivery_id = _seed_dead_delivery(hook.id)

        with _auth_as(_may_impersonate(hook_principal.subject)):
            created = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body(name, hook.principal),
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

        with _auth_as(_may_impersonate(powerless.subject)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("doomed", powerless.subject),
            )
            listed = client.get("/hooks", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert "workflow:ops:incident:run" in resp.text
        assert listed.json()["total"] == 0

    def test_update_denies_rebinding_to_a_principal_that_cannot_run(self, client, hook):
        powerless = _seed_principal("viewer")

        with _auth_as(_may_impersonate(powerless.subject)):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"principal": powerless.subject},
            )
            got = client.get(f"/hooks/{hook.name}", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert got.json()["principal"] == hook.principal

    def test_test_fire_denies_a_principal_revoked_after_creation(
        self,
        client,
        hook,
        hook_principal,
    ):
        """The test fire starts a real execution as the hook's principal, so a
        principal whose rights were revoked since the hook was created must not
        get one more run out of it."""
        from flux.models import RepositoryFactory
        from flux.security.principals import PrincipalRegistry

        repo = RepositoryFactory.create_repository()
        PrincipalRegistry(session_factory=lambda: repo.session()).revoke_role(
            hook_principal.id,
            "operator",
        )

        with _auth_as(_may_impersonate(hook_principal.subject)):
            resp = client.post(f"/hooks/{hook.name}/test", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert "workflow:ops:incident:run" in resp.text

    def test_create_denies_a_disabled_principal(self, client):
        """Disabled is a state of the principal, not a missing permission —
        so it reads as one, the way the schedule path answers it."""
        from flux.models import RepositoryFactory
        from flux.security.principals import PrincipalRegistry

        _seed_workflow("ops", "incident")
        principal = _seed_principal("operator")
        repo = RepositoryFactory.create_repository()
        PrincipalRegistry(session_factory=lambda: repo.session()).set_enabled(principal.id, False)

        with _auth_as(_may_impersonate(principal.subject)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("disabled-principal", principal.subject),
            )
            listed = client.get("/hooks", headers=_headers())

        assert resp.status_code == 400, resp.text
        assert "disabled" in resp.text.lower()
        assert "permission" not in resp.text.lower()
        assert listed.json()["total"] == 0

    def test_create_denies_a_principal_that_is_not_a_service_account(self, client):
        """A hook runs unattended under the bound identity, so binding a human
        principal would be honoured at fire time and surprise the operator —
        the reason the schedule path insists on a service account too."""
        from flux.models import RepositoryFactory
        from flux.security.principals import PrincipalRegistry

        _seed_workflow("ops", "incident")
        repo = RepositoryFactory.create_repository()
        registry = PrincipalRegistry(session_factory=lambda: repo.session())
        person = registry.create(
            type="user",
            subject=f"person-{uuid.uuid4().hex[:8]}",
            external_issuer="flux",
        )
        registry.assign_role(person.id, "operator")

        with _auth_as(_may_impersonate(person.subject)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("human-principal", person.subject),
            )

        assert resp.status_code == 400, resp.text
        assert person.subject in resp.text


class TestBindingAPrincipalIsImpersonation:
    """A hook fires its target under the bound principal's roles, so choosing
    that principal is impersonation — ``hook:*:create`` alone must not let an
    operator borrow an admin service account and run workflows as it."""

    def test_create_denies_binding_without_the_grant(self, client, hook_principal):
        with _auth_as(_as("operator")):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("borrowed", hook_principal.subject),
            )
            listed = client.get("/hooks", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert f"principal:{hook_principal.subject}:impersonate" in resp.text
        assert listed.json()["total"] == 0

    def test_create_allows_the_holder_of_the_grant(self, client, hook_principal):
        with _auth_as(_may_impersonate(hook_principal.subject)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("granted", hook_principal.subject),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["principal"] == hook_principal.subject

    def test_the_grant_is_per_subject(self, client, hook_principal):
        """A grant for one principal must not authorize binding another."""
        other = _seed_principal("operator")

        with _auth_as(_may_impersonate(other.subject)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("wrong-grant", hook_principal.subject),
            )

        assert resp.status_code == 403, resp.text
        assert f"principal:{hook_principal.subject}:impersonate" in resp.text

    def test_update_rebind_requires_the_grant(self, client, hook):
        target = _seed_principal("operator")

        with _auth_as(_as("operator")):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"principal": target.subject},
            )
            got = client.get(f"/hooks/{hook.name}", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert f"principal:{target.subject}:impersonate" in resp.text
        assert got.json()["principal"] == hook.principal

    def test_update_rebind_succeeds_with_the_grant(self, client, hook):
        target = _seed_principal("operator")

        with _auth_as(_may_impersonate(target.subject)):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"principal": target.subject},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["principal"] == target.subject

    def test_resending_the_same_principal_is_not_a_rebind(self, client, hook):
        """A client sending the hook's current principal back is not choosing
        an identity, so it must not need the grant."""
        with _auth_as(_as("operator")):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"principal": hook.principal, "max_attempts": 3},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["max_attempts"] == 3

    def test_binding_an_unknown_principal_is_refused(self, client):
        _seed_workflow("ops", "incident")

        with _auth_as(_may_impersonate("no-such-principal")):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("ghost-principal", "no-such-principal"),
            )

        assert resp.status_code == 400, resp.text
        assert "no-such-principal" in resp.text
        # A missing principal and an under-privileged one are different
        # problems with different fixes, so they do not share a message.
        assert "impersonate" not in resp.text
        assert "permission" not in resp.text.lower()


class TestReAimingAHookIsImpersonationToo:
    """Rebinding is not the only way to borrow a principal: pointing an
    existing hook at another workflow, or at other events, runs the identity
    it already carries against a target the caller chose. Without this, an
    operator holding only ``hook:*`` could take any admin-bound hook, re-aim
    it and fire it — the escalation the create-time gate closed, by a longer
    route."""

    def test_re_aiming_the_target_requires_the_grant(self, client, hook, hook_principal):
        _seed_workflow("ops", "payout")

        with _auth_as(_as("operator")):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"workflow_ref": "ops/payout"},
            )
            got = client.get(f"/hooks/{hook.name}", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert f"principal:{hook_principal.subject}:impersonate" in resp.text
        assert got.json()["workflow_ref"] == "ops/incident"

    def test_re_aiming_the_selectors_requires_the_grant(self, client, hook, hook_principal):
        with _auth_as(_as("operator")):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"selectors": ["execution:*:*:completed"]},
            )
            got = client.get(f"/hooks/{hook.name}", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert f"principal:{hook_principal.subject}:impersonate" in resp.text
        assert got.json()["selectors"] == ["execution:*:*:failed"]

    def test_re_aiming_succeeds_with_the_grant(self, client, hook, hook_principal):
        _seed_workflow("ops", "payout")

        with _auth_as(_may_impersonate(hook_principal.subject)):
            resp = client.put(
                f"/hooks/{hook.name}",
                headers=_headers(),
                json={"workflow_ref": "ops/payout", "selectors": ["execution:*:*:completed"]},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["workflow_ref"] == "ops/payout"

    def test_the_stop_button_still_needs_no_grant(self, client, hook):
        """``enabled=false`` is what an operator reaches for when a hook is
        misbehaving. Gating it on a grant would jam it exactly then."""
        with _auth_as(_as("operator")):
            resp = client.put(f"/hooks/{hook.name}", headers=_headers(), json={"enabled": False})

        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is False

    def test_the_delivery_budget_still_needs_no_grant(self, client, hook):
        with _auth_as(_as("operator")):
            resp = client.put(f"/hooks/{hook.name}", headers=_headers(), json={"max_attempts": 2})

        assert resp.status_code == 200, resp.text
        assert resp.json()["max_attempts"] == 2

    def test_test_fire_requires_the_grant(self, client, hook, hook_principal):
        """The test fire is not configuration: it starts the target as the
        hook's principal, token minted and provenance stamped. Firing someone
        else's identity is the act the grant governs."""
        from flux.models import ExecutionContextModel, RepositoryFactory

        with _auth_as(_as("operator")):
            resp = client.post(f"/hooks/{hook.name}/test", headers=_headers())

        assert resp.status_code == 403, resp.text
        assert f"principal:{hook_principal.subject}:impersonate" in resp.text
        with RepositoryFactory.create_repository().session() as session:
            assert session.query(ExecutionContextModel).count() == 0

    def test_test_fire_succeeds_with_the_grant(self, client, hook, hook_principal):
        with _auth_as(_may_impersonate(hook_principal.subject)):
            resp = client.post(f"/hooks/{hook.name}/test", headers=_headers())

        assert resp.status_code == 200, resp.text
        assert resp.json()["execution_id"]


class TestPrincipalsAreNamedBySubject:
    """A hook names its principal the way the docs, the CLI and the principals
    API do — by subject, as a schedule names its service account."""

    def test_a_hook_is_bound_by_subject(self, client, hook_principal):
        with _auth_as(_may_impersonate(hook_principal.subject)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("by-subject", hook_principal.subject),
            )
            got = client.get("/hooks/by-subject", headers=_headers())

        assert resp.status_code == 200, resp.text
        assert resp.json()["principal"] == hook_principal.subject
        assert got.json()["principal"] == hook_principal.subject

    def test_a_principal_id_is_not_a_subject(self, client, hook_principal):
        """The id used to be accepted, and reported a permissions failure for
        what was a lookup miss."""
        with _auth_as(_may_impersonate(hook_principal.id)):
            resp = client.post(
                "/hooks",
                headers=_headers(),
                json=_create_body("by-id", hook_principal.id),
            )

        assert resp.status_code == 400, resp.text
        assert "not found" in resp.text.lower()
