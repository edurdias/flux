"""Unknown execution IDs must return 404, not 500.

``ContextManager.get()`` raises ``ExecutionContextNotFoundError`` for unknown
IDs; the execution-lookup endpoints translate that into a 404 and re-raise their
own ``HTTPException``s instead of collapsing everything into a 500.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration
from flux.server import Server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path / 'e404.db'}")
    Configuration.get().reset()
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'e404.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api(), raise_server_exceptions=False)
    Configuration.get().reset()
    DatabaseRepository._engines.clear()


UNKNOWN = "does-not-exist"


def test_workflow_status_unknown_execution_returns_404(client):
    r = client.get(f"/workflows/ns/wf/status/{UNKNOWN}")
    assert r.status_code == 404


def test_workflow_cancel_unknown_execution_returns_404(client):
    r = client.get(f"/workflows/ns/wf/cancel/{UNKNOWN}")
    assert r.status_code == 404
