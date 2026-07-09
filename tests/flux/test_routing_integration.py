"""Integration: routing policies and worker metrics across real components.

The unit tiers test the DSL, the AST extractor, and the dispatch evaluator in
isolation; these tests close the loops between them — source registration
through the real catalog feeding real batch dispatch, metrics persisting
through the real registry, and the pong route landing metrics in the
database and back out of GET /workers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.catalogs import WorkflowCatalog
from flux.domain.execution_context import ExecutionContext
from tests.flux.test_dispatch_batch import (
    _register_worker,
    clean_env,  # noqa: F401 - pytest fixture
)

ROUTED_SOURCE = b"""
from flux import workflow
from flux.routing import score, prefer, least, label, load, input


@workflow.with_options(
    routing=score(
        prefer(label("tier") == input("tier"), weight=10),
        least(load()),
    ),
)
async def tiered(ctx):
    return ctx.input
"""


def test_source_registration_feeds_dispatch(clean_env):  # noqa: F811
    """Close the loop: decorator source -> AST extraction -> catalog save ->
    WorkflowModel metadata -> batch dispatch honors the policy."""
    cm, registry = clean_env
    gold = _register_worker(registry, "gold-w", labels={"tier": "gold"})
    silver = _register_worker(registry, "silver-w", labels={"tier": "silver"})

    catalog = WorkflowCatalog.create()
    catalog.save(catalog.parse(ROUTED_SOURCE))

    saved = catalog.get("default", "tiered")
    workflow_routing = (saved.metadata or {}).get("routing")
    assert workflow_routing is not None, "policy lost between parse and save"

    ctx = cm.save(
        ExecutionContext(
            workflow_id=saved.id,
            workflow_namespace="default",
            workflow_name="tiered",
            input={"tier": "silver"},
        ),
    )

    assignments = cm.next_executions_batch([gold, silver], limit=10)

    assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "silver-w")]


def test_registry_metrics_round_trip(clean_env):  # noqa: F811
    """record_metrics persists; a fresh registry read (any replica) sees it."""
    _, registry = clean_env
    _register_worker(registry, "metered-w")

    registry.record_metrics("metered-w", {"fitness": 0.9, "flux.cpu_percent": 12.5})

    from flux.worker_registry import DatabaseWorkerRegistry

    fresh = DatabaseWorkerRegistry()
    assert fresh.get("metered-w").metrics == {"fitness": 0.9, "flux.cpu_percent": 12.5}
    (listed,) = (w for w in fresh.list() if w.name == "metered-w")
    assert listed.metrics == {"fitness": 0.9, "flux.cpu_percent": 12.5}


@pytest.fixture
def server_client(tmp_path):
    """Real server + real SQLite DB, worker auth stubbed."""
    from flux.config import Configuration
    from flux.models import DatabaseRepository
    from flux.security.identity import FluxIdentity
    from flux.server import Server

    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'integration.db'}")
    DatabaseRepository._engines.clear()
    server = Server("127.0.0.1", 0)

    worker_identity = FluxIdentity(subject="w1", roles=frozenset({"worker"}))
    mock_auth = MagicMock()

    async def mock_authenticate(token):
        return worker_identity

    async def mock_is_authorized(identity, permission):
        return True

    mock_auth.authenticate = mock_authenticate
    mock_auth.is_authorized = mock_is_authorized

    with (
        patch.object(server, "_verify_worker_identity"),
        patch.object(server, "_record_heartbeat", new=AsyncMock()),
        patch("flux.security.dependencies._get_auth_service", return_value=mock_auth),
    ):
        yield server, TestClient(server._create_api())
    DatabaseRepository._engines.clear()


def _register_via_registry(name: str) -> None:
    from flux.worker_registry import WorkerRegistry
    from tests.flux.test_dispatch_batch import _make_resources, _make_runtime

    WorkerRegistry.create().register(
        name=name,
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
    )


def test_pong_metrics_persist_and_surface_in_workers_list(server_client):
    """Pong -> validation -> in-memory + DB -> GET /workers, no mocks in
    the storage path."""
    server, client = server_client
    _register_via_registry("w1")

    resp = client.post(
        "/workers/w1/pong",
        json={"healthy": True, "metrics": {"fitness": 0.7, "flux.cpu_percent": 3.0}},
    )
    assert resp.status_code == 200

    (worker,) = (w for w in client.get("/workers").json() if w["name"] == "w1")
    assert worker["metrics"] == {"fitness": 0.7, "flux.cpu_percent": 3.0}

    # Persisted, not just cached: a fresh registry read sees the same values.
    from flux.worker_registry import DatabaseWorkerRegistry

    assert DatabaseWorkerRegistry().get("w1").metrics == {
        "fitness": 0.7,
        "flux.cpu_percent": 3.0,
    }


def test_pong_metrics_total_cap_boundary(server_client):
    """The server admits merged payloads up to 64 keys (provider budget is
    32, built-ins ride on top) and drops anything larger."""
    server, client = server_client
    _register_via_registry("w1")

    at_cap = {f"k{i}": 1.0 for i in range(64)}
    assert client.post("/workers/w1/pong", json={"metrics": at_cap}).status_code == 200
    from flux.worker_registry import DatabaseWorkerRegistry

    assert DatabaseWorkerRegistry().get("w1").metrics == at_cap

    over_cap = {f"k{i}": 1.0 for i in range(65)}
    assert client.post("/workers/w1/pong", json={"metrics": over_cap}).status_code == 200
    # Dropped, previous snapshot intact — a hint channel, never an error.
    assert DatabaseWorkerRegistry().get("w1").metrics == at_cap
