"""Anonymous state-change is classified by what a route does, not its verb.

With auth disabled and allow_anonymous unset — the shipped default — the
middleware refuses anonymous mutating requests. It inferred "mutating" from
the HTTP method, so a GET that changes state slipped through: cancel commits
start_cancel and voids pending approvals, and the worker connect route bumps
a connection generation and marks the worker online.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "anon_mutation.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(
        database_url=f"sqlite:///{db_path}",
        workers={"bootstrap_token": "test-bootstrap-token"},
        security={
            "encryption": {"encryption_key": "test-encryption-key"},
            # tests/conftest.py permits anonymous mutations for convenience;
            # this module is the one exercising the deny path.
            "auth": {"allow_anonymous": False},
        },
    )

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api(), raise_server_exceptions=False)

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


def _seed(namespace: str, name: str) -> str:
    from flux import ExecutionContext
    from flux.context_managers import ContextManager
    from flux.models import RepositoryFactory, WorkflowModel

    eid = f"exec-{uuid.uuid4().hex[:8]}"
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=f"{namespace}/{name}",
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace=namespace,
            ),
        )
        session.commit()
    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"{namespace}/{name}",
        workflow_namespace=namespace,
        workflow_name=name,
        input=None,
        execution_id=eid,
    )
    ContextManager.create().save(ctx)
    return eid


def test_anonymous_post_is_refused(client):
    """Control: the verb-based rule already covers this."""
    r = client.post("/workflows/default/anything/run/sync", json={})
    assert r.status_code == 401, r.text


def test_anonymous_cancel_is_refused(client):
    """A GET that commits start_cancel and voids pending approvals."""
    suffix = uuid.uuid4().hex[:6]
    name = f"cancelme_{suffix}"
    eid = _seed("default", name)

    r = client.get(f"/workflows/default/{name}/cancel/{eid}")
    assert r.status_code == 401, r.text


def test_anonymous_worker_connect_is_refused(client):
    """A GET that bumps the connection generation and marks a worker online."""
    r = client.get("/workers/some-worker/connect")
    assert r.status_code == 401, r.text


def test_anonymous_reads_still_work(client):
    """The guard must not turn into a blanket deny."""
    assert client.get("/workflows").status_code == 200
    assert client.get("/executions").status_code == 200


def test_allow_anonymous_permits_the_mutating_get(client):
    """The opt-out still applies to the newly classified routes."""
    from flux.config import Configuration

    suffix = uuid.uuid4().hex[:6]
    name = f"allowme_{suffix}"
    eid = _seed("default", name)

    settings = Configuration.get().settings
    original = settings.security.auth.allow_anonymous
    settings.security.auth.allow_anonymous = True
    try:
        r = client.get(f"/workflows/default/{name}/cancel/{eid}")
        assert r.status_code != 401, r.text
    finally:
        settings.security.auth.allow_anonymous = original


def test_anonymous_head_cancel_does_not_reach_the_handler(client):
    """FastAPI's APIRoute does not add HEAD to a GET route (bare Starlette
    Route does), so this is 405 today. Asserted anyway: if HEAD ever becomes
    routable, it must not be a way around the guard."""
    suffix = uuid.uuid4().hex[:6]
    name = f"headcancel_{suffix}"
    eid = _seed("default", name)

    r = client.head(f"/workflows/default/{name}/cancel/{eid}")
    assert r.status_code in (401, 405), r.status_code

    from flux.context_managers import ContextManager

    assert ContextManager.create().get(eid).state.value != "CANCELLING"


def test_anonymous_head_worker_connect_does_not_reach_the_handler(client):
    r = client.head("/workers/some-worker/connect")
    assert r.status_code in (401, 405), r.status_code
