"""Tests for the batch dispatch queries used by the event-driven dispatcher."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.context_managers import DatabaseContextManager
from flux.domain import ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.models import ExecutionContextModel, RepositoryFactory
from flux.worker_registry import (
    DatabaseWorkerRegistry,
    WorkerResourcesInfo,
    WorkerRuntimeInfo,
)


@pytest.fixture
def clean_env():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False

        cm = DatabaseContextManager()
        registry = DatabaseWorkerRegistry()
        yield cm, registry

    if os.path.exists(db_path):
        os.unlink(db_path)


def _make_runtime():
    return WorkerRuntimeInfo(os_name="Linux", os_version="6.0", python_version="3.12.0")


def _make_resources():
    return WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=4,
        memory_total=8_000_000_000,
        memory_available=8_000_000_000,
        disk_total=100_000_000_000,
        disk_free=100_000_000_000,
        gpus=[],
    )


def _register_worker(registry, name, labels=None):
    registry.register(
        name=name,
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels,
    )
    return registry.get(name)


def _create_workflow(name, namespace="default", affinity=None, requests=None):
    from flux.models import WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        wf = WorkflowModel(
            id=f"{namespace}/{name}",
            name=name,
            version=1,
            imports=[],
            source=b"async def placeholder(ctx): pass",
            namespace=namespace,
            affinity=affinity,
            requests=requests,
        )
        session.add(wf)
        session.commit()
        return wf.id


def _create_execution(cm, workflow_id, namespace="default", name="test"):
    ctx = ExecutionContext(
        workflow_id=workflow_id,
        workflow_namespace=namespace,
        workflow_name=name,
    )
    return cm.save(ctx)


def _force_state(execution_id, state, worker_name=None):
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, execution_id)
        model.state = state
        model.worker_name = worker_name
        session.commit()


def _states(cm):
    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        rows = session.query(
            ExecutionContextModel.execution_id,
            ExecutionContextModel.state,
            ExecutionContextModel.worker_name,
        ).all()
        return {r[0]: (r[1], r[2]) for r in rows}


def test_batch_spreads_work_across_workers(clean_env):
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")
    w2 = _register_worker(registry, "w2")
    wf_id = _create_workflow("plain")
    for _ in range(4):
        _create_execution(cm, wf_id, name="plain")

    assignments = cm.next_executions_batch([w1, w2], limit=10)

    assert len(assignments) == 4
    per_worker = {"w1": 0, "w2": 0}
    for ctx, worker_name in assignments:
        assert ctx.state == ExecutionState.SCHEDULED
        per_worker[worker_name] += 1
    assert per_worker == {"w1": 2, "w2": 2}


def test_batch_respects_limit_and_leaves_rest_created(clean_env):
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")
    wf_id = _create_workflow("plain")
    ids = [_create_execution(cm, wf_id, name="plain").execution_id for _ in range(5)]

    assignments = cm.next_executions_batch([w1], limit=3)

    assert len(assignments) == 3
    states = _states(cm)
    created = [i for i in ids if states[i][0] == ExecutionState.CREATED]
    assert len(created) == 2


def test_batch_respects_affinity_and_skips_unmatchable(clean_env):
    cm, registry = clean_env
    plain_worker = _register_worker(registry, "plain-worker")
    gpu_worker = _register_worker(registry, "gpu-worker", labels={"gpu": "true"})

    gpu_wf = _create_workflow("gpu-flow", affinity={"gpu": "true"})
    tpu_wf = _create_workflow("tpu-flow", affinity={"tpu": "true"})
    gpu_exec = _create_execution(cm, gpu_wf, name="gpu-flow")
    tpu_exec = _create_execution(cm, tpu_wf, name="tpu-flow")

    assignments = cm.next_executions_batch([plain_worker, gpu_worker], limit=10)

    assert [(c.execution_id, w) for c, w in assignments] == [
        (gpu_exec.execution_id, "gpu-worker"),
    ]
    states = _states(cm)
    assert states[tpu_exec.execution_id][0] == ExecutionState.CREATED


def test_batch_prefers_least_loaded_worker(clean_env):
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")
    w2 = _register_worker(registry, "w2")
    wf_id = _create_workflow("plain")

    # w1 already runs two executions; a single new one must land on w2.
    for _ in range(2):
        busy = _create_execution(cm, wf_id, name="plain")
        _force_state(busy.execution_id, ExecutionState.RUNNING, worker_name="w1")
    pending = _create_execution(cm, wf_id, name="plain")

    assignments = cm.next_executions_batch([w1, w2], limit=10)

    assert assignments[0][0].execution_id == pending.execution_id
    assert assignments[0][1] == "w2"


def test_cancellations_batch_only_returns_targeted_workers(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1")
    _register_worker(registry, "w2")
    wf_id = _create_workflow("plain")
    e1 = _create_execution(cm, wf_id, name="plain")
    e2 = _create_execution(cm, wf_id, name="plain")
    _force_state(e1.execution_id, ExecutionState.CANCELLING, worker_name="w1")
    _force_state(e2.execution_id, ExecutionState.CANCELLING, worker_name="w2")

    result = cm.next_cancellations_batch(["w1"], limit=10)

    assert [c.execution_id for c in result] == [e1.execution_id]


def test_resumes_batch_sticky_then_unassigned(clean_env):
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")
    w2 = _register_worker(registry, "w2")
    wf_id = _create_workflow("plain")

    sticky = _create_execution(cm, wf_id, name="plain")
    _force_state(sticky.execution_id, ExecutionState.RESUMING, worker_name="w1")
    floating = _create_execution(cm, wf_id, name="plain")
    _force_state(floating.execution_id, ExecutionState.RESUMING, worker_name=None)

    assignments = cm.next_resumes_batch([w1, w2], limit=10)

    by_id = {ctx.execution_id: (ctx, worker) for ctx, worker in assignments}
    assert by_id[sticky.execution_id][1] == "w1"
    assert by_id[sticky.execution_id][0].state == ExecutionState.RESUME_SCHEDULED
    # The floating resume lands on some connected worker.
    assert by_id[floating.execution_id][1] in ("w1", "w2")
    states = _states(cm)
    assert states[sticky.execution_id][0] == ExecutionState.RESUME_SCHEDULED
    assert states[floating.execution_id][0] == ExecutionState.RESUME_SCHEDULED


def test_batch_with_no_workers_is_a_noop(clean_env):
    cm, registry = clean_env
    wf_id = _create_workflow("plain")
    e = _create_execution(cm, wf_id, name="plain")

    assert cm.next_executions_batch([], limit=10) == []
    assert cm.next_cancellations_batch([], limit=10) == []
    assert cm.next_resumes_batch([], limit=10) == []
    assert _states(cm)[e.execution_id][0] == ExecutionState.CREATED


def _register_capped_worker(registry, name, capacity, labels=None):
    registry.register(
        name=name,
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels,
        max_concurrent_executions=capacity,
    )
    return registry.get(name)


def test_batch_never_exceeds_worker_capacity(clean_env):
    cm, registry = clean_env
    w1 = _register_capped_worker(registry, "w1", capacity=2)
    wf_id = _create_workflow("plain")
    for _ in range(5):
        _create_execution(cm, wf_id, name="plain")

    assignments = cm.next_executions_batch([w1], limit=10)

    assert len(assignments) == 2
    states = _states(cm)
    created = [s for s, _ in states.values() if s == ExecutionState.CREATED]
    assert len(created) == 3  # held back, not lost


def test_batch_overflows_to_worker_with_free_slots(clean_env):
    cm, registry = clean_env
    capped = _register_capped_worker(registry, "capped", capacity=1)
    roomy = _register_capped_worker(registry, "roomy", capacity=10)
    wf_id = _create_workflow("plain")
    for _ in range(4):
        _create_execution(cm, wf_id, name="plain")

    assignments = cm.next_executions_batch([capped, roomy], limit=10)

    per_worker: dict[str, int] = {}
    for _, worker_name in assignments:
        per_worker[worker_name] = per_worker.get(worker_name, 0) + 1
    assert len(assignments) == 4
    assert per_worker["capped"] == 1
    assert per_worker["roomy"] == 3


def test_poll_path_respects_capacity(clean_env):
    cm, registry = clean_env
    w1 = _register_capped_worker(registry, "w1", capacity=1)
    wf_id = _create_workflow("plain")

    busy = _create_execution(cm, wf_id, name="plain")
    _force_state(busy.execution_id, ExecutionState.RUNNING, worker_name="w1")
    _create_execution(cm, wf_id, name="plain")

    # w1 is at capacity: the legacy poll query must not hand it more work.
    assert cm.next_execution(w1) is None

    _force_state(busy.execution_id, ExecutionState.COMPLETED, worker_name="w1")
    assert cm.next_execution(w1) is not None


def test_unlimited_capacity_workers_keep_legacy_behavior(clean_env):
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")  # no capacity advertised -> unlimited
    wf_id = _create_workflow("plain")
    for _ in range(3):
        _create_execution(cm, wf_id, name="plain")

    assignments = cm.next_executions_batch([w1], limit=10)
    assert len(assignments) == 3
