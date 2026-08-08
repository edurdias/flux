"""The ``agent:`` config prefix is reserved to the agent routes.

``POST/PUT /admin/agents`` gate code-carrying fields (``tools_file``,
``workflow_file``, an inline skills bundle) behind ``workflow:*:*:register``,
because ``agents/agent_chat`` ``exec``s ``tools_file`` on the worker. But the
definition the template actually loads is the mirror in the *configs* store
(``agent:<name>``), and the generic config endpoints write any key under the
weaker ``config:*:manage``. Reserving the prefix at the HTTP boundary closes
that door; ``AgentManager`` writes the mirror in-process and is unaffected.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_config_prefix.db"
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


# tools_file is an inert string here: the point of the test is that this value
# never reaches a worker, so nothing ever exec's it. Its content only has to
# look like what an attacker would plant.
PAYLOAD = {
    "name": "pwn",
    "model": "openai/gpt-4o",
    "system_prompt": "hi",
    "tools_file": "import os; os.system('id')",
}


def test_config_route_refuses_the_agent_prefix(client):
    resp = client.post("/admin/configs", json={"name": "agent:pwn", "value": PAYLOAD})
    assert resp.status_code == 403, resp.text

    # Nothing was written, so agent_chat cannot load it.
    read_back = client.get("/admin/configs/agent:pwn")
    assert read_back.status_code == 404, read_back.text


def test_config_delete_refuses_the_agent_prefix(client):
    resp = client.request("DELETE", "/admin/configs/agent:victim")
    assert resp.status_code == 403, resp.text


def test_ordinary_config_names_still_work(client):
    name = f"ordinary_{uuid.uuid4().hex[:6]}"
    saved = client.post("/admin/configs", json={"name": name, "value": {"k": "v"}})
    assert saved.status_code == 200, saved.text

    deleted = client.request("DELETE", f"/admin/configs/{name}")
    assert deleted.status_code == 200, deleted.text


def test_agent_manager_still_publishes_the_mirror(client):
    """The reservation is at the HTTP boundary only — the agent path that
    legitimately owns the prefix writes it in-process and must keep working."""
    from flux.agents.manager import _config_key
    from flux.config_manager import ConfigManager

    key = _config_key(f"legit_{uuid.uuid4().hex[:6]}")
    ConfigManager.current().save(key, {"model": "ollama/llama3"})
    assert ConfigManager.current().get(key) is not None
