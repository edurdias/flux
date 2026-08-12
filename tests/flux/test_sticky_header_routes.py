"""Route-level tests for the X-Flux-Preferred-Worker sticky-routing hint.

The run endpoint sanitizes the caller-supplied header (trim, bound, drop
invalid) and threads the surviving value into the execution row in the same
insert transaction. Dispatch-side behavior is covered in
test_dispatch_batch.py; these tests pin the HTTP boundary.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration
from flux.server import Server

SOURCE = b"""
from flux import workflow


@workflow
async def sticky_probe(ctx):
    return "ok"
"""


@pytest.fixture
def client(tmp_path):
    # Only override the DB URL; the autouse fixture in tests/conftest.py seeds
    # bootstrap-token / encryption / anonymous-auth defaults.
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'sticky.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    server = Server("127.0.0.1", 0)
    client = TestClient(server._create_api())

    files = {"file": ("flow.py", SOURCE, "text/x-python")}
    assert client.post("/workflows", files=files).status_code == 200

    yield client
    DatabaseRepository._engines.clear()


def _register_worker(name: str) -> None:
    """A binding is rejected for a name no worker ever registered."""
    from flux.worker_registry import (
        WorkerRegistry,
        WorkerResourcesInfo,
        WorkerRuntimeInfo,
    )

    WorkerRegistry.create().register(
        name=name,
        runtime=WorkerRuntimeInfo(os_name="Linux", os_version="6.0", python_version="3.12.0"),
        packages=[],
        resources=WorkerResourcesInfo(
            cpu_total=1,
            cpu_available=1,
            memory_total=1_000_000,
            memory_available=1_000_000,
            disk_total=1_000_000,
            disk_free=1_000_000,
            gpus=[],
        ),
    )


def _run_with_header(client, header_value: str | None) -> str:
    headers = {}
    if header_value is not None:
        headers["X-Flux-Preferred-Worker"] = header_value
    resp = client.post(
        "/workflows/default/sticky_probe/run/async",
        json=None,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["execution_id"]


def _preferred_worker(execution_id: str) -> str | None:
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        row = session.get(ExecutionContextModel, execution_id)
        assert row is not None
        return row.preferred_worker


def test_valid_hint_is_persisted_on_the_row(client):
    execution_id = _run_with_header(client, "worker-1")
    assert _preferred_worker(execution_id) == "worker-1"


def test_hint_is_trimmed(client):
    execution_id = _run_with_header(client, "  worker-1  ")
    assert _preferred_worker(execution_id) == "worker-1"


def test_whitespace_only_hint_is_dropped(client):
    execution_id = _run_with_header(client, "   ")
    assert _preferred_worker(execution_id) is None


def test_oversized_hint_is_dropped(client):
    execution_id = _run_with_header(client, "w" * 257)
    assert _preferred_worker(execution_id) is None


def test_boundary_length_hint_is_kept(client):
    execution_id = _run_with_header(client, "w" * 256)
    assert _preferred_worker(execution_id) == "w" * 256


def test_absent_header_leaves_no_hint(client):
    execution_id = _run_with_header(client, None)
    assert _preferred_worker(execution_id) is None


class TestRequireWorkerHeader:
    """X-Flux-Require-Worker (issue #187) is the binding counterpart, and
    takes the opposite validation policy: the hint drops a bad value, the
    constraint must reject it. Dropping would dispatch the execution anywhere,
    which the caller cannot distinguish from the binding being honoured.
    """

    def _run(self, client, value=None, preferred=None, register=True):
        headers = {}
        if value is not None:
            if register and value.strip():
                _register_worker(value.strip())
            headers["X-Flux-Require-Worker"] = value
        if preferred is not None:
            headers["X-Flux-Preferred-Worker"] = preferred
        return client.post(
            "/workflows/default/sticky_probe/run/async",
            json=None,
            headers=headers,
        )

    def _required_worker(self, execution_id):
        from flux.models import ExecutionContextModel, RepositoryFactory

        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            return session.get(ExecutionContextModel, execution_id).required_worker

    def test_valid_binding_is_persisted(self, client):
        resp = self._run(client, "worker-1")
        assert resp.status_code == 200, resp.text
        assert self._required_worker(resp.json()["execution_id"]) == "worker-1"

    def test_binding_is_trimmed(self, client):
        resp = self._run(client, "  worker-1  ")
        assert self._required_worker(resp.json()["execution_id"]) == "worker-1"

    @pytest.mark.parametrize("bad", ["   ", "w" * 257])
    def test_invalid_binding_is_rejected_not_dropped(self, client, bad):
        resp = self._run(client, bad)
        assert resp.status_code == 400
        assert "X-Flux-Require-Worker" in resp.text

    def test_boundary_length_is_accepted(self, client):
        resp = self._run(client, "w" * 256)
        assert resp.status_code == 200
        assert self._required_worker(resp.json()["execution_id"]) == "w" * 256

    def test_binding_supersedes_the_hint(self, client):
        """Not a 400: FluxClient.for_current_execution() injects the hint as a
        client-level default, so an in-workflow caller adding a binding would
        be rejected for a header it never set. The binding leaves one eligible
        worker anyway, so the hint has nothing left to order."""
        resp = self._run(client, "worker-1", preferred="worker-2")

        assert resp.status_code == 200, resp.text
        execution_id = resp.json()["execution_id"]
        assert self._required_worker(execution_id) == "worker-1"
        assert _preferred_worker(execution_id) is None

    def test_absent_header_leaves_no_binding(self, client):
        resp = self._run(client)
        assert self._required_worker(resp.json()["execution_id"]) is None

    def test_unknown_worker_is_rejected(self, client):
        """A name no worker ever had can only park forever, and park_ttl
        defaults to no bound — so it fails at submission instead."""
        resp = self._run(client, "never-registered", register=False)

        assert resp.status_code == 400
        assert "unknown worker" in resp.text

    def test_registered_but_offline_worker_is_accepted(self, client):
        """Binding to a worker that is currently down is legitimate: it parks
        until that worker returns. Only an unknown name is refused."""
        _register_worker("offline-1")
        resp = self._run(client, "offline-1", register=False)

        assert resp.status_code == 200, resp.text
