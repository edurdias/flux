"""Agent-declared hooks (declaration path 3): AgentDefinition.hooks
validation/escalation, and the admin routes' registration/replace/delete
wiring. Auth is off here, matching the rest of tests/flux/ -- permission
enforcement itself follows the same pattern tests/security/test_hook_authz.py
already exercises for path 1 and is not re-proven per-path here."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from flux.agents.types import AgentDefinition
from flux.config import Configuration
from flux.hooks.registry import HookRegistry


class TestAgentDefinitionHooks:
    def test_hooks_defaults_to_empty(self):
        definition = AgentDefinition(name="a", model="openai/gpt-4o", system_prompt="hi")
        assert definition.hooks == []

    def test_hooks_entries_are_validated_and_normalized(self):
        definition = AgentDefinition(
            name="a",
            model="openai/gpt-4o",
            system_prompt="hi",
            hooks=[
                {
                    "on": "execution:agents:agent_chat:completed",
                    "workflow": "ops/x",
                    "principal": "p",
                },
            ],
        )
        assert definition.hooks == [
            {
                "on": "execution:agents:agent_chat:completed",
                "workflow": "ops/x",
                "principal": "p",
                "name": None,
                "max_attempts": 5,
            },
        ]

    def test_a_malformed_hook_selector_is_rejected(self):
        with pytest.raises(ValidationError):
            AgentDefinition(
                name="a",
                model="openai/gpt-4o",
                system_prompt="hi",
                hooks=[{"on": "not-a-selector", "workflow": "ops/x", "principal": "p"}],
            )

    def test_requires_code_upload_permission_is_true_when_hooks_declared(self):
        definition = AgentDefinition(
            name="a",
            model="openai/gpt-4o",
            system_prompt="hi",
            hooks=[
                {
                    "on": "execution:agents:agent_chat:completed",
                    "workflow": "ops/x",
                    "principal": "p",
                },
            ],
        )
        assert definition.requires_code_upload_permission() is True

    def test_requires_code_upload_permission_is_false_with_no_hooks(self):
        definition = AgentDefinition(name="a", model="openai/gpt-4o", system_prompt="hi")
        assert definition.requires_code_upload_permission() is False


@pytest.fixture
def db(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'agent_hooks.db'}")
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


def _agent_payload(**overrides):
    payload = {
        "name": "helper",
        "model": "openai/gpt-4o",
        "system_prompt": "hi",
        "hooks": [
            {
                "on": "execution:agents:agent_chat:completed",
                "workflow": "ops/notify",
                "principal": "notifier",
            },
        ],
    }
    payload.update(overrides)
    return payload


class TestAgentDeclaredHookRegistration:
    def test_creating_an_agent_creates_its_declared_hook(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()

        resp = client.post("/admin/agents", json=_agent_payload())

        assert resp.status_code == 200, resp.text
        owned = [h for h in client.get("/hooks").json()["hooks"] if h["owner_type"] == "agent"]
        assert len(owned) == 1
        assert owned[0]["owner_ref"] == "helper"

    def test_creating_without_a_runnable_target_is_rejected(self, client):
        _seed_principal()
        # "ops/notify" is never seeded.

        resp = client.post("/admin/agents", json=_agent_payload())

        assert resp.status_code in (400, 403, 404), resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_updating_with_the_same_hooks_keeps_the_same_row(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        first_id = client.get("/hooks").json()["hooks"][0]["id"]

        # A PUT that re-declares the identical, unchanged hook must not
        # touch its identity -- the derived name is content-addressed, so
        # an identical spec re-derives the identical name every time.
        resp = client.put("/admin/agents/helper", json=_agent_payload())

        assert resp.status_code == 200, resp.text
        hooks = client.get("/hooks").json()["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["id"] == first_id
        assert hooks[0]["selectors"] == ["execution:agents:agent_chat:completed"]

    def test_updating_with_a_changed_selector_replaces_the_row(self, client):
        # An unnamed hook's identity is derived from (on, workflow,
        # principal) -- see flux/hooks/registry.py::HookRegistry.
        # _derive_hook_name. Editing `on` is therefore a *different*
        # declared identity, not an in-place edit of the old one: the old
        # row (and its delivery history) is deleted as no-longer-declared,
        # and a new row is created under the new derived name. A hook that
        # must keep a stable identity across an edited selector needs an
        # explicit `name=`.
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        first_id = client.get("/hooks").json()["hooks"][0]["id"]

        updated = _agent_payload(
            hooks=[
                {
                    "on": "execution:agents:agent_chat:failed",
                    "workflow": "ops/notify",
                    "principal": "notifier",
                },
            ],
        )
        resp = client.put("/admin/agents/helper", json=updated)

        assert resp.status_code == 200, resp.text
        hooks = client.get("/hooks").json()["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["id"] != first_id
        assert hooks[0]["selectors"] == ["execution:agents:agent_chat:failed"]

    def test_updating_without_hooks_removes_them(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        assert len(client.get("/hooks").json()["hooks"]) == 1

        resp = client.put(
            "/admin/agents/helper",
            json={"name": "helper", "model": "openai/gpt-4o", "system_prompt": "hi"},
        )

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_deleting_the_agent_deletes_its_owned_hooks(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        assert len(client.get("/hooks").json()["hooks"]) == 1

        resp = client.delete("/admin/agents/helper")

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_updating_with_a_hook_name_that_collides_with_another_owners_hook_is_409(
        self,
        client,
    ):
        # Regression for a bug where admin_update_agent mapped every
        # ValueError from AgentManager.update() to 404 -- including a hook
        # name conflict, which DatabaseAgentManager.update() used to turn
        # into a plain ValueError indistinguishable from "agent not found".
        # A conflict must surface as 409, matching admin_create_agent.
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())

        # A hook already owned by someone else holds the name the update
        # below will declare explicitly.
        HookRegistry.create().create_hook(
            name="already-taken",
            selectors=["execution:*:*:failed"],
            workflow_ref="ops/notify",
            principal="notifier",
            owner_type="user",
            owner_ref="admin",
        )

        updated = _agent_payload(
            hooks=[
                {
                    "on": "execution:agents:agent_chat:completed",
                    "workflow": "ops/notify",
                    "principal": "notifier",
                    "name": "already-taken",
                },
            ],
        )
        resp = client.put("/admin/agents/helper", json=updated)

        assert resp.status_code == 409, resp.text
        assert resp.status_code != 404
