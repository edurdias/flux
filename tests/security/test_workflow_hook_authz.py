"""Authorization tests for workflow-declared hooks, registered via
``POST /workflows`` rather than ``POST /hooks``.

Wiring counterpart to ``tests/security/test_hook_authz.py``'s coverage of the
direct ``/hooks`` CRUD surface: a workflow's ``hooks=[hook.run(...)]`` kwarg
reaches the same two escalation guards through a different door
(``flux/api/workflow_routes.py::workflows_save``), so this file proves those
guards actually fire on *this* integration point too, with auth enabled and
the real role -> permission resolution -- not just on the ``/hooks`` route.

1. ``hook:*:create`` gates declaring a hook at all -- a caller who can
   register workflows must not thereby mint hooks.
2. ``principal:<subject>:impersonate`` gates naming which principal a
   declared hook fires as -- the same impersonation rule
   ``_require_may_fire_as`` (Task 1) enforces on every other hook-creating
   route.

Both checks run before ``catalog.save(...)``, so a denial must abort the
*whole* registration -- not just skip the hook -- leaving zero rows of
either kind.
"""

from __future__ import annotations

import io
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.security.identity import FluxIdentity


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flux server app backed by a fresh on-disk SQLite database."""
    db_path = tmp_path / "wf_hook_authz.db"
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


def _seed_role(name: str, permissions: list[str]) -> str:
    from flux.models import RepositoryFactory
    from flux.security.models import RoleModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(RoleModel(name=name, permissions=permissions))
        session.commit()
    return name


def _identity_with(*permissions: str) -> FluxIdentity:
    """An identity holding exactly the given permissions -- not a built-in
    role, so a test asserting on the *absence* of a permission is not
    accidentally granted it by a wildcard elsewhere in one (e.g.
    ``operator``'s ``hook:*`` already covers ``hook:*:create``)."""
    role = _seed_role(f"custom-{uuid.uuid4().hex[:8]}", list(permissions))
    return FluxIdentity(subject=f"subject-{uuid.uuid4().hex[:6]}", roles=frozenset({role}))


def _may_impersonate(subject: str) -> FluxIdentity:
    """An operator who also holds the grant to bind ``subject``. ``operator``
    already carries ``workflow:*:*:register`` and ``hook:*`` (which covers
    ``hook:*:create``), so this identity holds all three permissions this
    integration point requires -- the same helper shape as
    ``test_hook_authz.py``'s."""
    role = _seed_role(
        f"wf_hook_imp_{uuid.uuid4().hex[:6]}",
        [f"principal:{subject}:impersonate"],
    )
    return FluxIdentity(
        subject=f"operator-{uuid.uuid4().hex[:6]}",
        roles=frozenset({"operator", role}),
    )


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


def _seed_principal(role: str | None = None):
    """A service-account principal, optionally holding ``role`` -- needed so
    the principal itself can pass ``_require_runnable_target``'s
    ``workflow:<ns>:<wf>:run`` check, which is evaluated fresh against the
    *principal's* own roles (``flux.hooks.dispatch.authorize_hook_principal``), not the caller's."""
    from flux.models import RepositoryFactory
    from flux.security.principals import PrincipalRegistry

    repo = RepositoryFactory.create_repository()
    registry = PrincipalRegistry(session_factory=lambda: repo.session())
    principal = registry.create(
        type="service_account",
        subject=f"notifier-{uuid.uuid4().hex[:8]}",
        external_issuer="flux",
    )
    if role:
        registry.assign_role(principal.id, role)
    return principal


def _source_for(principal: str) -> str:
    return f"""
from flux import workflow
from flux.hooks import hook


@workflow.with_options(
    namespace="release",
    hooks=[
        hook.run(
            on="execution:release:notify_on_fail:failed",
            workflow="ops/notify",
            principal="{principal}",
        ),
    ],
)
async def notify_on_fail(ctx):
    return ctx.input
"""


def _upload(client, source: str, filename: str = "wf.py"):
    return client.post(
        "/workflows",
        headers=_headers(),
        files={"file": (filename, io.BytesIO(source.encode()), "text/x-python")},
    )


def _workflow_row_count(namespace: str, name: str) -> int:
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        return session.query(WorkflowModel).filter_by(namespace=namespace, name=name).count()


def _owned_hook_count(namespace: str, name: str) -> int:
    from flux.hooks.registry import HookRegistry

    return len(
        HookRegistry.create().list_owned_hooks(
            owner_type="workflow",
            owner_ref=f"{namespace}/{name}",
        ),
    )


class TestHookCreateEscalation:
    """``hook:*:create`` gates declaring a hook through workflow
    registration, the same way it gates ``POST /hooks`` directly."""

    def test_declaring_without_hook_create_is_rejected_and_registers_nothing(self, client):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")
        identity = _identity_with("workflow:release:*:register")

        with _auth_as(identity):
            resp = _upload(client, _source_for(principal.subject))

        assert resp.status_code == 403, resp.text
        assert "hook:*:create" in resp.text
        assert _workflow_row_count("release", "notify_on_fail") == 0
        assert _owned_hook_count("release", "notify_on_fail") == 0


class TestImpersonationEscalation:
    """``principal:<subject>:impersonate`` gates naming which principal a
    declared hook fires as, the same way it gates ``POST /hooks``
    directly."""

    def test_declaring_without_the_impersonation_grant_is_rejected_and_registers_nothing(
        self,
        client,
    ):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")
        identity = _identity_with("workflow:release:*:register", "hook:*:create")

        with _auth_as(identity):
            resp = _upload(client, _source_for(principal.subject))

        assert resp.status_code == 403, resp.text
        assert f"principal:{principal.subject}:impersonate" in resp.text
        assert _workflow_row_count("release", "notify_on_fail") == 0
        assert _owned_hook_count("release", "notify_on_fail") == 0


class TestFullyAuthorizedRegistration:
    def test_holding_all_three_permissions_creates_the_hook(self, client):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")

        with _auth_as(_may_impersonate(principal.subject)):
            resp = _upload(client, _source_for(principal.subject))

        assert resp.status_code == 200, resp.text
        assert _workflow_row_count("release", "notify_on_fail") == 1
        assert _owned_hook_count("release", "notify_on_fail") == 1
