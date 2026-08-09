"""The agent code-upload gate applies to the config mirror too.

``POST/PUT /admin/agents`` require ``workflow:*:*:register`` for definitions
carrying ``tools_file``/``workflow_file``/an inline skills bundle, because
``agents/agent_chat`` execs ``tools_file`` on the worker. The definition the
template loads is the ``agent:<name>`` mirror in the config store, so the same
rule has to hold on ``POST /admin/configs`` — otherwise ``config:*:manage``
sidesteps it. Definitions that ship no code stay writable there, which is a
documented capability (``tests/e2e/test_config_and_agents.py``).
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.security.identity import FluxIdentity

BENIGN = {"model": "openai/gpt-4o", "system_prompt": "You are helpful.", "planning": False}
# Inert string: the test asserts this value never gets stored, so nothing execs it.
SHIPS_CODE = {**BENIGN, "tools_file": "import os; os.system('id')"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_config_gate.db"
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


class _AuthProxy:
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
    settings = Configuration.get().settings
    was_enabled = settings.security.auth.enabled
    was_api_keys = settings.security.auth.api_keys.enabled
    settings.security.auth.enabled = True
    settings.security.auth.api_keys.enabled = True
    try:
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=_AuthProxy(real_service, identity),
        ):
            yield
    finally:
        settings.security.auth.enabled = was_enabled
        settings.security.auth.api_keys.enabled = was_api_keys


def _headers():
    return {"Authorization": "Bearer fake-token"}


def test_config_manager_may_not_upload_agent_code(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_role(f"cfg_only_{suffix}", ["config:*:manage", "config:*:read"])
    identity = FluxIdentity(subject="intruder", roles=frozenset({f"cfg_only_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/admin/configs",
            headers=_headers(),
            json={"name": f"agent:pwn_{suffix}", "value": SHIPS_CODE},
        )
    assert resp.status_code == 403, resp.text

    read_back = client.get(f"/admin/configs/agent:pwn_{suffix}")
    assert read_back.status_code == 404, read_back.text


def test_register_permission_allows_agent_code(client):
    suffix = uuid.uuid4().hex[:6]
    _seed_role(f"cfg_reg_{suffix}", ["config:*:manage", "workflow:*:*:register"])
    identity = FluxIdentity(subject="developer", roles=frozenset({f"cfg_reg_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/admin/configs",
            headers=_headers(),
            json={"name": f"agent:ok_{suffix}", "value": SHIPS_CODE},
        )
    assert resp.status_code == 200, resp.text


def test_agent_config_without_code_stays_writable(client):
    """Matches tests/e2e/test_config_and_agents.py::test_agent_config_storage —
    storing a code-free agent definition as a config is supported."""
    suffix = uuid.uuid4().hex[:6]
    _seed_role(f"cfg_plain_{suffix}", ["config:*:manage"])
    identity = FluxIdentity(subject="operator", roles=frozenset({f"cfg_plain_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/admin/configs",
            headers=_headers(),
            json={"name": f"agent:plain_{suffix}", "value": BENIGN},
        )
        assert resp.status_code == 200, resp.text

        removed = client.request(
            "DELETE",
            f"/admin/configs/agent:plain_{suffix}",
            headers=_headers(),
        )
        assert removed.status_code == 200, removed.text


def test_json_string_payloads_are_inspected(client):
    """The CLI sends the value as a JSON string, so the gate must look inside
    it rather than only at dicts."""
    suffix = uuid.uuid4().hex[:6]
    _seed_role(f"cfg_str_{suffix}", ["config:*:manage"])
    identity = FluxIdentity(subject="intruder", roles=frozenset({f"cfg_str_{suffix}"}))

    with _auth_as(identity):
        resp = client.post(
            "/admin/configs",
            headers=_headers(),
            json={"name": f"agent:str_{suffix}", "value": json.dumps(SHIPS_CODE)},
        )
    assert resp.status_code == 403, resp.text


def test_ordinary_config_names_are_unaffected(client):
    name = f"ordinary_{uuid.uuid4().hex[:6]}"
    saved = client.post("/admin/configs", json={"name": name, "value": {"k": "v"}})
    assert saved.status_code == 200, saved.text
    assert client.request("DELETE", f"/admin/configs/{name}").status_code == 200
