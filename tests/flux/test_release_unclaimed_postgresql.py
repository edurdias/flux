"""Release-vs-claim races on PostgreSQL, where they actually interleave.

SQLite serialises writers, so the compare-and-set and the claim's row lock
are only exercised for real here: each test holds one side's lock open in
its own transaction and drives the other side from a thread.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from flux.config import Configuration
from flux.context_managers import DatabaseContextManager
from flux.domain import ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.models import ExecutionContextModel, RepositoryFactory

_DB_URL = os.environ.get("FLUX_DATABASE_URL", "")

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        not _DB_URL.startswith("postgresql://"),
        reason="requires a PostgreSQL FLUX_DATABASE_URL",
    ),
]


@pytest.fixture
def cm():
    from flux.models import DatabaseRepository, WorkflowModel

    Configuration.get().override(database_url=_DB_URL, database_type="postgresql")
    DatabaseRepository._engines.clear()
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.query(ExecutionContextModel).delete()
        if session.get(WorkflowModel, "default/release-race") is None:
            session.add(
                WorkflowModel(
                    id="default/release-race",
                    name="release-race",
                    version=1,
                    imports=[],
                    source=b"async def wf(ctx): pass",
                    namespace="default",
                ),
            )
        session.commit()
    yield DatabaseContextManager()
    DatabaseRepository._engines.clear()


def _worker(name: str):
    from flux.worker_registry import (
        DatabaseWorkerRegistry,
        WorkerResourcesInfo,
        WorkerRuntimeInfo,
    )

    registry = DatabaseWorkerRegistry()
    registry.register(
        name=name,
        runtime=WorkerRuntimeInfo(os_name="Linux", os_version="6", python_version="3.12"),
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
    return registry.get(name)


def _schedule_to(cm, worker) -> str:
    ctx = cm.save(
        ExecutionContext(
            workflow_id="default/release-race",
            workflow_namespace="default",
            workflow_name="release-race",
        ),
    )
    assignments = cm.next_executions_batch([worker], limit=10)
    assert [c.execution_id for c, _ in assignments] == [ctx.execution_id]
    return ctx.execution_id


def _row(execution_id: str):
    with RepositoryFactory.create_repository().session() as session:
        model = session.get(ExecutionContextModel, execution_id)
        return model.state, model.worker_name, model.claim_generation


def _in_thread(fn):
    result: dict = {}

    def run():
        try:
            result["value"] = fn()
        except BaseException as e:  # surfaced by the caller
            result["error"] = e

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def test_release_waits_for_a_claim_holding_the_row_and_then_leaves_it(cm):
    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)

    with cm.session() as claiming:
        model = cm._fenced_assignment(claiming, execution_id, w1, ExecutionState.SCHEDULED, 1)
        thread, result = _in_thread(lambda: cm.release_unclaimed(worker_name="w1"))
        time.sleep(0.5)
        assert thread.is_alive()  # blocked on the claim's row lock
        model.state = ExecutionState.CLAIMED
        claiming.commit()

    thread.join(10)
    assert "error" not in result
    assert result["value"] == []
    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 1)


def test_a_fenced_claim_waiting_on_a_release_is_refused(cm):
    from flux.errors import StaleClaimError

    w1 = _worker("w1")
    execution_id = _schedule_to(cm, w1)
    with cm.session() as releasing:
        seen = (
            releasing.query(
                ExecutionContextModel.execution_id,
                ExecutionContextModel.state,
                ExecutionContextModel.worker_name,
                ExecutionContextModel.claim_generation,
                ExecutionContextModel.required_worker,
            )
            .filter(ExecutionContextModel.execution_id == execution_id)
            .one()
        )
        assert cm._release_assignment(releasing, seen)
        thread, result = _in_thread(lambda: cm.claim(execution_id, w1, expected_generation=1))
        time.sleep(0.5)
        assert thread.is_alive()
        releasing.commit()

    thread.join(10)
    assert isinstance(result.get("error"), StaleClaimError)
    assert _row(execution_id) == (ExecutionState.CREATED, None, 2)


def test_an_unfenced_claim_waiting_on_a_release_owns_the_row_alone(cm):
    """A pre-fencing worker's claim racing the release: it waits for the
    release, takes the released row under its lock, and the dispatcher then
    has nothing to hand anyone else."""
    w1, w2 = _worker("w1"), _worker("w2")
    execution_id = _schedule_to(cm, w1)
    with cm.session() as releasing:
        seen = (
            releasing.query(
                ExecutionContextModel.execution_id,
                ExecutionContextModel.state,
                ExecutionContextModel.worker_name,
                ExecutionContextModel.claim_generation,
                ExecutionContextModel.required_worker,
            )
            .filter(ExecutionContextModel.execution_id == execution_id)
            .one()
        )
        assert cm._release_assignment(releasing, seen)
        thread, result = _in_thread(lambda: cm.claim(execution_id, w1))
        time.sleep(0.5)
        releasing.commit()

    thread.join(10)
    assert "error" not in result
    assert cm.next_executions_batch([w2], limit=10) == []
    assert _row(execution_id) == (ExecutionState.CLAIMED, "w1", 2)


def test_an_unfenced_claim_waits_for_a_redispatch_and_then_defers_to_it(cm):
    """w1's claim reaches the released row while the dispatcher holds it,
    mid re-assignment to w2. It must decide on the row the dispatcher
    commits, not on the CREATED row it would have read without the lock."""
    from flux.errors import ExecutionError

    w1 = _worker("w1")
    _worker("w2")
    execution_id = _schedule_to(cm, w1)
    cm.release_unclaimed(worker_name="w1")

    with cm.session() as dispatching:
        model = (
            dispatching.query(ExecutionContextModel)
            .filter(ExecutionContextModel.execution_id == execution_id)
            .with_for_update()
            .one()
        )
        thread, result = _in_thread(lambda: cm.claim(execution_id, w1))
        time.sleep(0.5)
        assert thread.is_alive()  # waiting on the dispatcher's row lock
        model.state = ExecutionState.SCHEDULED
        model.worker_name = "w2"
        model.claim_generation = 3
        dispatching.commit()

    thread.join(10)
    assert isinstance(result.get("error"), ExecutionError)
    assert _row(execution_id) == (ExecutionState.SCHEDULED, "w2", 3)


def test_the_claim_deadline_compares_assignment_times_on_postgresql(cm):
    from datetime import datetime, timedelta, timezone

    from flux.domain.events import ExecutionEventType
    from flux.models import ExecutionEventModel

    w1 = _worker("w1")
    stale = _schedule_to(cm, w1)
    fresh = _schedule_to(cm, w1)
    with RepositoryFactory.create_repository().session() as session:
        for event in session.query(ExecutionEventModel).filter(
            ExecutionEventModel.execution_id == stale,
            ExecutionEventModel.type == ExecutionEventType.WORKFLOW_SCHEDULED,
        ):
            event.time = datetime.now(timezone.utc) - timedelta(seconds=120)
        session.commit()

    released = cm.release_unclaimed(
        scheduled_before=datetime.now(timezone.utc) - timedelta(seconds=60),
    )

    assert released == [stale]
    assert _row(fresh) == (ExecutionState.SCHEDULED, "w1", 1)
