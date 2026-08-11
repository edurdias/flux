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

    def _run(self, client, value=None, preferred=None):
        headers = {}
        if value is not None:
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

    def test_both_headers_is_rejected(self, client):
        resp = self._run(client, "worker-1", preferred="worker-2")
        assert resp.status_code == 400
        assert "mutually exclusive" in resp.text

    def test_both_headers_rejected_even_when_the_hint_is_invalid(self, client):
        """Checked on the raw headers: sanitizing the hint to None first would
        hide that the caller sent two contradictory directives."""
        resp = self._run(client, "worker-1", preferred="   ")
        assert resp.status_code == 400
        assert "mutually exclusive" in resp.text

    def test_absent_header_leaves_no_binding(self, client):
        resp = self._run(client)
        assert self._required_worker(resp.json()["execution_id"]) is None
