"""Redaction is a property of execution-read responses, not of one handler.

``redact_response`` was applied on two exits of ``GET /executions/{id}`` and
nowhere else, so the same DTO left unredacted through the SSE stream and the
workflow status routes — reachable with the same ``execution:*:read`` grant
that the redacted path requires.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

SECRET_VALUE = "sk-live-do-not-leak-0123456789"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "redaction_routes.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    # Resetting Configuration discards what tests/conftest.py seeds, and this
    # module is one of the few that actually encrypts a secret.
    Configuration.get().override(
        database_url=f"sqlite:///{db_path}",
        workers={"bootstrap_token": "test-bootstrap-token"},
        security={"encryption": {"encryption_key": "test-encryption-key"}},
    )

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api())

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


@pytest.fixture
def leaked_execution(client):
    """An execution whose output embeds a value the secret store knows.

    This is the case redaction.py exists for: a task returned something with a
    secret in it, and the value is now in the event log.
    """
    from flux.secret_managers import SecretManager
    from flux.security import redaction

    suffix = uuid.uuid4().hex[:6]
    name = f"leaky_{suffix}"
    eid = f"exec-leak-{suffix}"

    SecretManager.current().save(f"LEAK_KEY_{suffix}", SECRET_VALUE)

    from flux import ExecutionContext
    from flux.context_managers import ContextManager
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=f"default/{name}",
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace="default",
            ),
        )
        session.commit()

    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"default/{name}",
        workflow_namespace="default",
        workflow_name=name,
        input=None,
        execution_id=eid,
    )
    # Through the domain API: has_finished is derived from a WORKFLOW_COMPLETED
    # event rather than the state column, and the SSE stream only ends once it
    # is true — poking the row directly hangs the stream forever.
    ctx.start(eid)
    ctx.complete(eid, {"token": SECRET_VALUE})
    ContextManager.create().save(ctx)

    # The value cache is process-global and may predate the save above.
    redaction._cache = None

    try:
        yield name, eid
    finally:
        SecretManager.current().remove(f"LEAK_KEY_{suffix}")


def test_detailed_execution_read_is_redacted(client, leaked_execution):
    """Control: the exit that already called redact_response."""
    _name, eid = leaked_execution
    r = client.get(f"/executions/{eid}", params={"detailed": "true"})
    assert r.status_code == 200, r.text
    assert SECRET_VALUE not in r.text


def test_stream_mode_is_redacted(client, leaked_execution):
    """Same endpoint, same permission, one query parameter apart."""
    _name, eid = leaked_execution
    with client.stream(
        "GET",
        f"/executions/{eid}",
        params={"detailed": "true", "mode": "stream"},
    ) as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert SECRET_VALUE not in body


def test_workflow_status_is_redacted(client, leaked_execution):
    name, eid = leaked_execution
    r = client.get(f"/workflows/default/{name}/status/{eid}", params={"detailed": "true"})
    assert r.status_code == 200, r.text
    assert SECRET_VALUE not in r.text


def test_redaction_does_not_mangle_ordinary_output(client):
    """The marker must not appear where there is nothing to redact."""
    from flux import ExecutionContext
    from flux.context_managers import ContextManager
    from flux.models import RepositoryFactory, WorkflowModel

    suffix = uuid.uuid4().hex[:6]
    name, eid = f"plain_{suffix}", f"exec-plain-{suffix}"
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=f"default/{name}",
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace="default",
            ),
        )
        session.commit()
    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"default/{name}",
        workflow_namespace="default",
        workflow_name=name,
        input=None,
        execution_id=eid,
    )
    ctx.start(eid)
    ctx.complete(eid, {"result": "ordinary value"})
    ContextManager.create().save(ctx)

    r = client.get(f"/executions/{eid}", params={"detailed": "true"})
    assert r.status_code == 200, r.text
    assert json.loads(r.text)["output"]["result"] == "ordinary value"
