from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration
from flux.context_managers import DatabaseContextManager
from flux.domain.execution_context import ExecutionContext
from flux.domain.events import ExecutionState
from flux.models import DatabaseRepository, ExecutionContextModel, WorkerModel, WorkflowModel
from flux.server import Server
from flux.worker_registry import WorkerInfo


@pytest.fixture
def execution_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path / 'review.db'}")
    monkeypatch.setenv("FLUX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLUX_SECURITY__AUTH__ENABLED", "false")
    monkeypatch.setenv("FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS", "true")
    Configuration.get().reset()
    Configuration.get().override(
        database_url=f"sqlite:///{tmp_path / 'review.db'}",
        workers={"bootstrap_token": "review-only"},
        security={"encryption": {"encryption_key": "review-only"}},
    )
    DatabaseRepository._engines.clear()
    cm = DatabaseContextManager()
    with cm.session() as session:
        session.add(
            WorkflowModel(
                id="default/review",
                name="review",
                namespace="default",
                version=1,
                imports=[],
                source=b"async def review(ctx): pass",
            ),
        )
        session.add_all([WorkerModel(name="w1"), WorkerModel(name="w2")])
        session.commit()
    server = Server("127.0.0.1", 0)
    workers = [WorkerInfo(name="w1"), WorkerInfo(name="w2")]
    server._worker_info.update({worker.name: worker for worker in workers})
    ctx = cm.save(ExecutionContext("default/review", "default", "review"))
    yield cm, TestClient(server._create_api()), workers, ctx
    DatabaseRepository._engines.clear()
    Configuration.get().reset()


def test_stale_release_preserves_reassigned_claim(execution_env):
    cm, client, (w1, w2), ctx = execution_env
    cm.next_execution(w1)
    cm.claim(ctx.execution_id, w1)
    generation = cm.get_claim_generation(ctx.execution_id)
    original = DatabaseContextManager.unclaim
    interleaved = False

    def reassign_before_release(manager, execution_id, **kwargs):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            original(manager, execution_id)
            assert manager.next_execution(w2) is not None
            manager.claim(execution_id, w2)
            assert manager.get(execution_id).current_worker == "w2"
            assert manager.get_claim_generation(execution_id) > generation
        return original(manager, execution_id, **kwargs)

    with patch.object(DatabaseContextManager, "unclaim", reassign_before_release):
        response = client.post(
            f"/workers/w1/release/{ctx.execution_id}",
            headers={"X-Flux-Claim-Generation": str(generation)},
        )
    assert response.status_code == 409, response.text
    persisted = cm.get(ctx.execution_id)
    assert persisted.state == ExecutionState.CLAIMED
    assert persisted.current_worker == "w2"


def test_claim_race_preserves_completed_execution(execution_env):
    cm, client, (w1, _), ctx = execution_env
    cm.next_execution(w1)
    original = DatabaseContextManager.claim

    def complete_between_route_read_and_claim(manager, execution_id, worker):
        other = original(manager, execution_id, worker)
        other.start(execution_id)
        other.complete(execution_id, "already finished")
        manager.update(other, manager.get_claim_generation(execution_id))
        assert manager.get(execution_id).state == ExecutionState.COMPLETED
        return original(manager, execution_id, worker)

    with patch.object(DatabaseContextManager, "claim", complete_between_route_read_and_claim):
        response = client.post(f"/workers/w1/claim/{ctx.execution_id}")
    assert response.status_code == 409, response.text
    assert cm.get(ctx.execution_id).state == ExecutionState.COMPLETED


def test_same_terminal_checkpoint_preserves_summary_and_event_output(execution_env):
    cm, _, (w1, _), ctx = execution_env
    cm.next_execution(w1)
    first = cm.claim(ctx.execution_id, w1)
    generation = cm.get_claim_generation(ctx.execution_id)
    first.start(ctx.execution_id)
    first.complete(ctx.execution_id, "first")
    cm.update(first, generation)
    competing = ExecutionContext(
        "default/review",
        "default",
        "review",
        execution_id=ctx.execution_id,
    )
    competing.complete(ctx.execution_id, "second")
    cm.update(competing, generation)
    with cm.session() as session:
        assert session.get(ExecutionContextModel, ctx.execution_id).output == "first"
    assert cm.get(ctx.execution_id).output == "first"
    assert cm.get_summary(ctx.execution_id)["output"] == "first"


def test_claim_response_keeps_its_generation_when_reassigned_before_response(execution_env):
    cm, client, (w1, w2), ctx = execution_env
    cm.next_execution(w1)
    generation = cm.get_claim_generation(ctx.execution_id)
    original = DatabaseContextManager.claim

    def reassign_after_claim(manager, execution_id, worker):
        claimed = original(manager, execution_id, worker)
        manager.unclaim(execution_id)
        manager.next_execution(w2)
        original(manager, execution_id, w2)
        return claimed

    with patch.object(DatabaseContextManager, "claim", reassign_after_claim):
        response = client.post(f"/workers/w1/claim/{ctx.execution_id}")
    assert response.status_code == 200, response.text
    assert response.headers["X-Flux-Claim-Generation"] == str(generation)
    assert cm.get_claim_generation(ctx.execution_id) > generation
    assert cm.get(ctx.execution_id).current_worker == "w2"
