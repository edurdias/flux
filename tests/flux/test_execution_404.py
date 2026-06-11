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
def client(tmp_path):
    # Only override the DB URL. The autouse fixture in tests/conftest.py seeds
    # (and tears down) the bootstrap-token / encryption / auth defaults, so we
    # must not reset the Configuration singleton here.
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'e404.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api(), raise_server_exceptions=False)
    DatabaseRepository._engines.clear()


UNKNOWN = "does-not-exist"


def test_workflow_status_unknown_execution_returns_404(client):
    r = client.get(f"/workflows/ns/wf/status/{UNKNOWN}")
    assert r.status_code == 404


def test_workflow_cancel_unknown_execution_returns_404(client):
    r = client.get(f"/workflows/ns/wf/cancel/{UNKNOWN}")
    assert r.status_code == 404


def _seed_execution(execution_id: str, namespace: str, name: str) -> None:
    from flux.domain.events import ExecutionState
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            ExecutionContextModel(
                execution_id=execution_id,
                workflow_id="wid",
                workflow_name=name,
                input=None,
                workflow_namespace=namespace,
                state=ExecutionState.COMPLETED,
            ),
        )
        session.commit()


def test_status_rejects_cross_workflow_execution(client):
    # An execution that belongs to billing/charge must not be readable via a
    # different workflow's URL even with that workflow's read permission.
    _seed_execution("exec-x", "billing", "charge")
    r = client.get("/workflows/other/wf/status/exec-x")
    assert r.status_code == 404
    # Sanity: it is reachable under its own workflow.
    assert client.get("/workflows/billing/charge/status/exec-x").status_code == 200


def test_cancel_rejects_cross_workflow_execution(client):
    _seed_execution("exec-y", "billing", "charge")
    r = client.get("/workflows/other/wf/cancel/exec-y")
    assert r.status_code == 404
