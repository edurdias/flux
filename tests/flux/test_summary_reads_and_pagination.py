"""Summary reads without event hydration (D5) and opt-in listing pagination.

``ContextManager.get_summary`` / ``last_event_ordinal`` let status polls and
the sync-wait stream loop avoid loading and unpickling an execution's entire
event log; the routes exercise the ``GET /executions/{id}`` fast path and the
``limit``/``offset`` parameters on the workers/roles listings.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionContextNotFoundError
from flux.servers.models import ExecutionContext as ExecutionContextDTO


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A FluxServer app backed by a fresh on-disk SQLite database."""
    db_path = tmp_path / "summary_reads.db"
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


def _seed_execution(
    execution_id: str,
    *,
    events: list[ExecutionEvent] | None = None,
    state=None,
):
    from flux import ExecutionContext

    ctx: ExecutionContext = ExecutionContext(
        workflow_id="default/wf",
        workflow_namespace="default",
        workflow_name="wf",
        input={"n": 1},
        execution_id=execution_id,
        events=events or [],
        state=state,
    )
    return ContextManager.create().save(ctx)


def _completed_events() -> list[ExecutionEvent]:
    return [
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_STARTED,
            source_id="w",
            name="wf",
            value={"n": 1},
        ),
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_COMPLETED,
            source_id="w",
            name="wf",
            value=42,
        ),
    ]


class TestGetSummary:
    def test_matches_dto_summary(self, client):
        """The scalar-only read returns exactly what DTO.summary() derives
        from the fully hydrated context."""
        _seed_execution("exec-sum-1", events=_completed_events())
        manager = ContextManager.create()

        full = ExecutionContextDTO.from_domain(manager.get("exec-sum-1")).summary()
        light = manager.get_summary("exec-sum-1")
        assert light == full

    def test_paused_surfaces_pause_payload(self, client):
        events = [
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_STARTED,
                source_id="w",
                name="wf",
                value={"n": 1},
            ),
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_PAUSED,
                source_id="gate",
                name="wf",
                value={"output": {"waiting": "gate"}},
            ),
        ]
        from flux.domain import ExecutionState

        _seed_execution("exec-sum-paused", events=events, state=ExecutionState.PAUSED)
        manager = ContextManager.create()

        full = ExecutionContextDTO.from_domain(manager.get("exec-sum-paused")).summary()
        light = manager.get_summary("exec-sum-paused")
        assert light == full
        assert light["state"] == "PAUSED"
        assert light["output"] == {"waiting": "gate"}

    def test_missing_execution_raises(self, client):
        with pytest.raises(ExecutionContextNotFoundError):
            ContextManager.create().get_summary("nope")


class TestLastEventOrdinal:
    def test_advances_with_new_events(self, client):
        manager = ContextManager.create()
        _seed_execution("exec-ord-1", events=_completed_events()[:1])
        first = manager.last_event_ordinal("exec-ord-1")
        assert first is not None

        ctx = manager.get("exec-ord-1")
        ctx.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_COMPLETED,
                source_id="w",
                name="wf",
                value=1,
            ),
        )
        manager.save(ctx)
        assert manager.last_event_ordinal("exec-ord-1") > first

    def test_none_when_no_events(self, client):
        _seed_execution("exec-ord-empty")
        assert ContextManager.create().last_event_ordinal("exec-ord-empty") is None
        assert ContextManager.create().last_event_ordinal("missing") is None


class TestExecutionGetFastPath:
    def test_summary_response_shape_unchanged(self, client):
        """detailed=False must return the same body whether it came from the
        fast path or full hydration."""
        from flux.domain import ExecutionState

        _seed_execution(
            "exec-route-1",
            events=_completed_events(),
            state=ExecutionState.COMPLETED,
        )

        summary = client.get("/executions/exec-route-1").json()
        detailed = client.get("/executions/exec-route-1", params={"detailed": True}).json()

        assert summary["execution_id"] == "exec-route-1"
        assert summary["state"] == detailed["state"] == "COMPLETED"
        assert summary["output"] == 42
        assert summary["workflow_namespace"] == "default"
        assert summary["current_worker"] == detailed["current_worker"]
        # The summary body never carries the event log.
        assert "events" not in summary
        assert "events" in detailed

    def test_missing_execution_404(self, client):
        assert client.get("/executions/definitely-missing").status_code == 404


class TestListingPagination:
    def test_roles_limit_offset(self, client):
        client.post("/admin/roles", json={"name": "r-aaa", "permissions": ["x:y:z"]})
        client.post("/admin/roles", json={"name": "r-bbb", "permissions": ["x:y:z"]})

        everything = client.get("/admin/roles").json()
        assert len(everything) >= 2  # built-ins + the two above

        page = client.get("/admin/roles", params={"limit": 2, "offset": 1}).json()
        assert page == everything[1:3]

        assert client.get("/admin/roles", params={"limit": 0}).status_code == 422

    def test_workers_limit_offset(self, client):
        from flux.worker_registry import (
            WorkerRegistry,
            WorkerResourcesInfo,
            WorkerRuntimeInfo,
        )

        registry = WorkerRegistry.create()
        for name in ("w-aaa", "w-bbb", "w-ccc"):
            registry.register(
                name=name,
                runtime=WorkerRuntimeInfo(
                    os_name="Linux",
                    os_version="6",
                    python_version="3.12",
                ),
                packages=[],
                resources=WorkerResourcesInfo(
                    cpu_total=1,
                    cpu_available=1,
                    memory_total=1,
                    memory_available=1,
                    disk_total=1,
                    disk_free=1,
                    gpus=[],
                ),
            )

        everything = client.get("/workers").json()
        assert len(everything) == 3

        page = client.get("/workers", params={"limit": 1, "offset": 1}).json()
        assert len(page) == 1
        assert page[0]["name"] == everything[1]["name"]
