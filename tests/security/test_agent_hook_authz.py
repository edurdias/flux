"""Authorization tests for agent-declared hooks, registered via
``POST /admin/agents`` / ``PUT /admin/agents/{name}`` rather than
``POST /hooks``.

Wiring counterpart to ``tests/security/test_hook_authz.py``'s coverage of
the direct ``/hooks`` CRUD surface and ``tests/security/test_workflow_hook_authz.py``'s
coverage of the workflow-declared path (declaration path 2): an agent's
``hooks=[hook.run(...)]`` field reaches its own escalation guard plus the
shared impersonation guard through a third door
(``flux/api/admin_routes.py::admin_create_agent``/``admin_update_agent``),
so this file proves those guards actually fire on *this* integration point
too, with auth enabled and the real role -> permission resolution -- not
just on the ``/hooks`` route or the workflow-declared path, and not just
with auth disabled the way ``tests/flux/test_agent_hooks_admin.py`` covers
the plumbing.

Unlike workflow-declared hooks -- gated by a dedicated ``hook:*:create``
check in addition to ``workflow:{namespace}:*:register`` -- agent-declared
hooks fold into ``AgentDefinition.requires_code_upload_permission()``: the
same ``workflow:*:*:register`` gate ``tools_file``/``workflow_file``/
``skills_dir`` already use. Declaring a hook on an agent is authorized the
same way shipping arbitrary code to a worker is, so there is no separate
``hook:*:create`` check on this path (see ``flux/api/admin_routes.py``,
Task 7 of the outbound-hooks slice-2 plan). This file exercises that gate,
plus:

1. ``requires_code_upload_permission()``'s ``workflow:*:*:register`` gate,
   now also covering ``hooks``.
2. ``principal:<subject>:impersonate`` gating which principal a declared
   hook fires as -- the same impersonation rule ``_require_may_fire_as``
   (Task 1) enforces on every other hook-creating route.

Both checks run before ``AgentManager.create()``/``update()``, so a denial
must abort the whole agent write -- not just skip the hook -- leaving zero
rows of either kind.
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
    """A Flux server app backed by a fresh on-disk SQLite database."""
    db_path = tmp_path / "agent_hook_authz.db"
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
    ``operator``'s ``agent:*:*`` already covers ``agent:*:create``)."""
    role = _seed_role(f"custom-{uuid.uuid4().hex[:8]}", list(permissions))
    return FluxIdentity(subject=f"subject-{uuid.uuid4().hex[:6]}", roles=frozenset({role}))


def _may_impersonate(subject: str) -> FluxIdentity:
    """An operator who also holds the grant to bind ``subject``. ``operator``
    already carries ``agent:*:*`` and ``workflow:*:*:register``, so this
    identity holds every permission this integration point requires -- the
    same helper shape as ``test_hook_authz.py``'s and
    ``test_workflow_hook_authz.py``'s."""
    role = _seed_role(
        f"agent_hook_imp_{uuid.uuid4().hex[:6]}",
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


def _agent_payload(name: str, principal: str, **overrides) -> dict:
    payload = {
        "name": name,
        "model": "openai/gpt-4o",
        "system_prompt": "hi",
        "hooks": [
            {
                "on": "execution:agents:agent_chat:completed",
                "workflow": "ops/notify",
                "principal": principal,
            },
        ],
    }
    payload.update(overrides)
    return payload


def _create_agent(client, name: str, principal: str, **overrides):
    return client.post(
        "/admin/agents",
        headers=_headers(),
        json=_agent_payload(name, principal, **overrides),
    )


def _agent_row_count(name: str) -> int:
    from flux.models import AgentModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        return session.query(AgentModel).filter_by(name=name).count()


def _owned_hook_count(name: str) -> int:
    from flux.hooks.registry import HookRegistry

    return len(HookRegistry.create().list_owned_hooks(owner_type="agent", owner_ref=name))


class TestCodeUploadEscalation:
    """``workflow:*:*:register`` -- via ``requires_code_upload_permission()``
    -- gates declaring a hook through agent creation/update, the same way it
    already gates ``tools_file``/``workflow_file``/``skills_dir``. Unlike the
    workflow-declared path there is no separate ``hook:*:create`` check
    here; this is the escalation gate for this integration point."""

    def test_creating_without_upload_permission_is_rejected_and_creates_nothing(self, client):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")
        identity = _identity_with("agent:*:create")

        with _auth_as(identity):
            resp = _create_agent(client, "helper", principal.subject)

        assert resp.status_code == 403, resp.text
        assert "workflow:*:*:register" in resp.text
        assert _agent_row_count("helper") == 0
        assert _owned_hook_count("helper") == 0

    def test_updating_to_add_hooks_without_upload_permission_is_rejected_and_leaves_it_unhooked(
        self,
        client,
    ):
        """An existing agent with no hooks, updated by an identity that can
        manage agents but not upload code, must not be able to bolt a hook
        on -- and the denial must not silently drop the hook while
        otherwise applying the update."""
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")
        from flux.agents.manager import AgentManager
        from flux.agents.types import AgentDefinition

        AgentManager.current().create(
            AgentDefinition(name="helper", model="openai/gpt-4o", system_prompt="hi"),
        )
        identity = _identity_with("agent:*:update")

        with _auth_as(identity):
            resp = client.put(
                "/admin/agents/helper",
                headers=_headers(),
                json=_agent_payload("helper", principal.subject, system_prompt="changed"),
            )

        assert resp.status_code == 403, resp.text
        assert "workflow:*:*:register" in resp.text
        assert _owned_hook_count("helper") == 0
        assert AgentManager.current().get("helper").system_prompt == "hi"


class TestImpersonationEscalation:
    """``principal:<subject>:impersonate`` gates naming which principal a
    declared hook fires as, the same way it gates ``POST /hooks`` and
    workflow-declared hooks directly."""

    def test_creating_without_the_impersonation_grant_is_rejected_and_creates_nothing(
        self,
        client,
    ):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")
        identity = _identity_with("agent:*:create", "workflow:*:*:register")

        with _auth_as(identity):
            resp = _create_agent(client, "helper", principal.subject)

        assert resp.status_code == 403, resp.text
        assert f"principal:{principal.subject}:impersonate" in resp.text
        assert _agent_row_count("helper") == 0
        assert _owned_hook_count("helper") == 0

    def test_updating_to_add_hooks_without_the_impersonation_grant_is_rejected(self, client):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")
        from flux.agents.manager import AgentManager
        from flux.agents.types import AgentDefinition

        AgentManager.current().create(
            AgentDefinition(name="helper", model="openai/gpt-4o", system_prompt="hi"),
        )
        identity = _identity_with("agent:*:update", "workflow:*:*:register")

        with _auth_as(identity):
            resp = client.put(
                "/admin/agents/helper",
                headers=_headers(),
                json=_agent_payload("helper", principal.subject, system_prompt="changed"),
            )

        assert resp.status_code == 403, resp.text
        assert f"principal:{principal.subject}:impersonate" in resp.text
        assert _owned_hook_count("helper") == 0
        assert AgentManager.current().get("helper").system_prompt == "hi"


class TestFullyAuthorizedRegistration:
    def test_holding_all_permissions_creates_the_agent_and_its_hook(self, client):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")

        with _auth_as(_may_impersonate(principal.subject)):
            resp = _create_agent(client, "helper", principal.subject)

        assert resp.status_code == 200, resp.text
        assert _agent_row_count("helper") == 1
        assert _owned_hook_count("helper") == 1

    def test_holding_all_permissions_updates_the_agent_and_reconciles_its_hook(self, client):
        _seed_workflow("ops", "notify")
        principal = _seed_principal("operator")

        with _auth_as(_may_impersonate(principal.subject)):
            _create_agent(client, "helper", principal.subject)
            resp = client.put(
                "/admin/agents/helper",
                headers=_headers(),
                json=_agent_payload("helper", principal.subject, system_prompt="changed"),
            )

        assert resp.status_code == 200, resp.text
        assert _owned_hook_count("helper") == 1
        from flux.agents.manager import AgentManager

        assert AgentManager.current().get("helper").system_prompt == "changed"
