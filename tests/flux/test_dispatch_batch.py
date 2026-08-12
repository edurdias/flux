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


def test_batch_honors_preferred_worker_hint(clean_env):
    """A sticky-routing hint beats least-loaded when the worker is eligible."""
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")
    w2 = _register_worker(registry, "w2")
    wf_id = _create_workflow("plain")

    # Load w2 so least-loaded would pick w1; the hint must still win.
    busy = _create_execution(cm, wf_id, name="plain")
    _force_state(busy.execution_id, ExecutionState.RUNNING, worker_name="w2")
    hinted = _create_execution(cm, wf_id, name="plain")
    cm.set_preferred_worker(hinted.execution_id, "w2")

    assignments = cm.next_executions_batch([w1, w2], limit=10)

    assigned = {ctx.execution_id: worker for ctx, worker in assignments}
    assert assigned[hinted.execution_id] == "w2"


def test_preferred_worker_hint_falls_back_when_ineligible(clean_env):
    """A hint naming a worker that is not dispatchable is ignored."""
    cm, registry = clean_env
    w1 = _register_worker(registry, "w1")
    wf_id = _create_workflow("plain")
    hinted = _create_execution(cm, wf_id, name="plain")
    cm.set_preferred_worker(hinted.execution_id, "ghost-worker")

    assignments = cm.next_executions_batch([w1], limit=10)

    assigned = {ctx.execution_id: worker for ctx, worker in assignments}
    assert assigned[hinted.execution_id] == "w1"


def test_preferred_worker_persists_in_the_insert_transaction(clean_env):
    """The hint is written with the insert, not a follow-up UPDATE — a
    dispatcher racing the submission must still see it."""
    cm, registry = clean_env
    _register_worker(registry, "w1")
    w2 = _register_worker(registry, "w2")
    wf_id = _create_workflow("plain")

    ctx = cm.save(
        ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name="plain"),
        preferred_worker="w2",
    )

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        row = session.get(ExecutionContextModel, ctx.execution_id)
        assert row.preferred_worker == "w2"

    assignments = cm.next_executions_batch([w2], limit=10)
    assert {c.execution_id: w for c, w in assignments}[ctx.execution_id] == "w2"


# ---------------------------------------------------------------------------
# require(...) affinity expressions (event-mode dispatch parity)
# ---------------------------------------------------------------------------


def _create_execution_with_input(cm, workflow_id, name, input):
    ctx = ExecutionContext(
        workflow_id=workflow_id,
        workflow_namespace="default",
        workflow_name=name,
        input=input,
    )
    return cm.save(ctx)


def test_batch_require_routes_by_input(clean_env):
    from flux.routing import input as input_, label, require

    cm, registry = clean_env
    eu = _register_worker(registry, "eu", labels={"region": "eu-west"})
    us = _register_worker(registry, "us", labels={"region": "us-east"})

    wf_id = _create_workflow(
        "infer",
        affinity=require(label("region") == input_("region")),
    )
    ctx_eu = _create_execution_with_input(cm, wf_id, "infer", {"region": "eu-west"})
    ctx_us = _create_execution_with_input(cm, wf_id, "infer", {"region": "us-east"})

    assignments = {
        ctx.execution_id: worker for ctx, worker in cm.next_executions_batch([eu, us], limit=10)
    }
    assert assignments == {ctx_eu.execution_id: "eu", ctx_us.execution_id: "us"}


def test_batch_require_unresolved_input_fails_execution(clean_env):
    from flux.routing import input as input_, label, require

    cm, registry = clean_env
    eu = _register_worker(registry, "eu", labels={"region": "eu-west"})

    wf_id = _create_workflow(
        "infer",
        affinity=require(label("region") == input_("region")),
    )
    bad = _create_execution_with_input(cm, wf_id, "infer", {"oops": 1})
    good = _create_execution_with_input(cm, wf_id, "infer", {"region": "eu-west"})

    assignments = cm.next_executions_batch([eu], limit=10)

    assert [ctx.execution_id for ctx, _ in assignments] == [good.execution_id]
    states = _states(cm)
    assert states[bad.execution_id][0] == ExecutionState.FAILED
    failed = cm.get(bad.execution_id)
    [event] = [e for e in failed.events if e.type.name == "WORKFLOW_FAILED"]
    assert "region" in str(event.value)


def test_batch_require_mismatch_parks_execution(clean_env):
    from flux.routing import input as input_, label, require

    cm, registry = clean_env
    us = _register_worker(registry, "us", labels={"region": "us-east"})

    wf_id = _create_workflow(
        "infer",
        affinity=require(label("region") == input_("region")),
    )
    ctx = _create_execution_with_input(cm, wf_id, "infer", {"region": "eu-west"})

    assert cm.next_executions_batch([us], limit=10) == []
    assert _states(cm)[ctx.execution_id][0] == ExecutionState.CREATED


def test_batch_require_when_gate(clean_env):
    from flux.routing import input as input_, label, require, when

    cm, registry = clean_env
    plain = _register_worker(registry, "plain", labels={"region": "eu"})
    dedicated = _register_worker(
        registry,
        "dedicated",
        labels={"region": "eu", "cap.dedicated": "true"},
    )

    wf_id = _create_workflow(
        "infer",
        affinity=require(
            label("region") == "eu",
            when(input_("tier") == "dedicated", label("cap.dedicated") == "true"),
        ),
    )
    gated = _create_execution_with_input(cm, wf_id, "infer", {"tier": "dedicated"})

    # Only the dedicated worker is eligible for the gated execution.
    assignments = cm.next_executions_batch([plain, dedicated], limit=10)
    assert [(ctx.execution_id, w) for ctx, w in assignments] == [
        (gated.execution_id, "dedicated"),
    ]


def test_batch_resume_respects_require_expression(clean_env):
    from flux.routing import input as input_, label, require

    cm, registry = clean_env
    us = _register_worker(registry, "us", labels={"region": "us-east"})
    eu = _register_worker(registry, "eu", labels={"region": "eu-west"})

    wf_id = _create_workflow(
        "infer",
        affinity=require(label("region") == input_("region")),
    )
    ctx = _create_execution_with_input(cm, wf_id, "infer", {"region": "eu-west"})
    _force_state(ctx.execution_id, ExecutionState.RESUMING, worker_name=None)

    assignments = cm.next_resumes_batch([us, eu], limit=10)
    assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "eu")]


def test_batch_pairs_require_floor_with_dynamic_prefer(clean_env):
    """require() as the hard floor, a dynamic-key prefer() as the soft
    preference: eligible workers are filtered by datacenter, then the one
    holding a warm cache copy of the requested dataset wins despite load."""
    from flux.models import WorkflowModel
    from flux.routing import input as input_, label, label_for, least, load, prefer, require, score

    cm, registry = clean_env
    warm = _register_worker(registry, "warm", labels={"dc": "eu", "cache.orders": "true"})
    cold = _register_worker(registry, "cold", labels={"dc": "eu"})
    other = _register_worker(registry, "other", labels={"dc": "us", "cache.orders": "true"})

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        wf = WorkflowModel(
            id="default/locality",
            name="locality",
            version=1,
            imports=[],
            source=b"async def placeholder(ctx): pass",
            affinity=require(label("dc") == input_("dc")),
            metadata={
                "routing": score(
                    prefer(label_for("cache.", input_("dataset")) == "true", weight=10),
                    least(load()),
                ),
            },
        )
        session.add(wf)
        session.commit()

    # Load the warm worker up so least(load()) alone would pick cold.
    busy_wf = _create_workflow("busy")
    for _ in range(3):
        ctx = _create_execution(cm, busy_wf, name="busy")
        _force_state(ctx.execution_id, ExecutionState.RUNNING, worker_name="warm")

    ctx = ExecutionContext(
        workflow_id="default/locality",
        workflow_namespace="default",
        workflow_name="locality",
        input={"dc": "eu", "dataset": "orders"},
    )
    cm.save(ctx)

    assignments = cm.next_executions_batch([warm, cold, other], limit=10)
    assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "warm")]


class TestRequiredWorkerBinding:
    """X-Flux-Require-Worker (issue #187): the execution runs on the named
    worker or nowhere. Falling back is the failure the binding exists to
    prevent — from outside it is indistinguishable from having been honoured.
    """

    def _bind(self, execution_id, worker_name):
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.get(ExecutionContextModel, execution_id).required_worker = worker_name
            session.commit()

    def test_batch_dispatches_only_to_the_bound_worker(self, clean_env):
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        w2 = _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")

        assignments = cm.next_executions_batch([w1, w2], limit=10)

        assert [name for _, name in assignments] == ["w2"]

    def test_batch_parks_rather_than_falling_back(self, clean_env):
        """The bound worker is absent from the fleet: nothing is dispatched
        and the row stays CREATED for the park-TTL sweep to fail loudly."""
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "absent-worker")

        assert cm.next_executions_batch([w1], limit=10) == []
        assert _states(cm)[ctx.execution_id][0] == ExecutionState.CREATED

    def test_poll_mode_honours_the_binding_on_an_unconstrained_workflow(self, clean_env):
        """The regression this guards: poll mode's unconstrained branch selects
        on the *workflow* having no affinity/requests/metadata and takes the
        first row, so without a row-level filter a bound execution would go to
        whichever worker polled first."""
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        w2 = _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")

        assert cm.next_execution(w1) is None
        claimed = cm.next_execution(w2)
        assert claimed is not None and claimed.execution_id == ctx.execution_id

    def test_unbound_executions_are_unaffected(self, clean_env):
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")

        assert [name for _, name in cm.next_executions_batch([w1], limit=10)] == ["w1"]
        assert _states(cm)[ctx.execution_id][0] == ExecutionState.SCHEDULED

    def test_resume_batch_honours_the_binding(self, clean_env):
        """A binding honoured only on first dispatch is a hole: a paused
        execution must not resume somewhere else."""
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        w2 = _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")
        _force_state(ctx.execution_id, ExecutionState.RESUMING, worker_name=None)

        assignments = cm.next_resumes_batch([w1, w2], limit=10)

        assert [name for _, name in assignments] == ["w2"]

    def test_park_diagnostic_names_the_bound_worker(self, clean_env):
        from datetime import datetime, timedelta, timezone

        cm, registry = clean_env
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "absent-worker")
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            model = session.get(ExecutionContextModel, ctx.execution_id)
            model.park_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()

        assert cm.fail_expired_parked() == [ctx.execution_id]

        loaded = cm.get(ctx.execution_id)
        assert loaded.has_failed
        assert "absent-worker" in str(loaded.output)

    def test_claim_refuses_a_worker_the_execution_is_not_bound_to(self, clean_env):
        """The dispatch queries filter on the binding, but /workers/{name}/claim
        is reachable by any registered worker and its fallback accepts a CREATED
        row — the state a bound execution waits in. Without this check the
        binding is advisory: a worker claims what it was not given."""
        from flux.errors import ExecutionError

        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")

        with pytest.raises(ExecutionError, match="bound to 'w2'"):
            cm.claim(ctx.execution_id, w1)

        assert _states(cm)[ctx.execution_id][0] == ExecutionState.CREATED

    def test_claim_allows_the_bound_worker(self, clean_env):
        cm, registry = clean_env
        w2 = _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")

        claimed = cm.claim(ctx.execution_id, w2)

        assert claimed.state == ExecutionState.CLAIMED

    def test_load_gate_does_not_block_a_binding(self, clean_env):
        """Poll mode is the default. The least-loaded gate spreads work, but
        bound work cannot be spread — without a bypass the execution parks
        while its worker is online and has capacity, purely because a peer
        happens to be emptier."""
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        w2 = _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")

        # Make w2 the busier worker so the load gate would reject it.
        for _ in range(3):
            busy = _create_execution(cm, wf_id, name="plain")
            _force_state(busy.execution_id, ExecutionState.RUNNING, worker_name="w2")

        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")

        claimed = cm.next_execution(w2)

        assert claimed is not None and claimed.execution_id == ctx.execution_id
        assert cm.next_execution(w1) is None


class TestBoundResumeBackstop:
    """A bound execution released back to RESUMING waits on one worker that
    may never return, and nothing else can take it. The park sweep covered
    CREATED only, so that row had no bound at all (issue #212)."""

    def _bind(self, execution_id, worker_name):
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.get(ExecutionContextModel, execution_id).required_worker = worker_name
            session.commit()

    def _row(self, execution_id):
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
            return model.state, model.worker_name, model.park_deadline

    def _expire(self, execution_id):
        from datetime import datetime, timedelta, timezone

        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
            model.park_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()

    def test_release_worker_starts_the_clock_for_a_bound_execution(self, clean_env):
        cm, registry = clean_env
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")
        _force_state(ctx.execution_id, ExecutionState.RESUMING, worker_name="w2")

        with patch("flux.config.Configuration.get") as cfg:
            cfg.return_value.settings.workers.park_ttl = 60
            cm.release_worker(ctx.execution_id)

        state, worker_name, deadline = self._row(ctx.execution_id)
        assert state == ExecutionState.RESUMING
        assert worker_name is None
        assert deadline is not None

    def test_clock_restarts_rather_than_reusing_the_submission_deadline(self, clean_env):
        """A long-running execution that pauses hours after submission would
        already be past its original deadline, and would be swept the instant
        it entered RESUMING."""
        from datetime import datetime, timedelta, timezone

        cm, registry = clean_env
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            model = session.get(ExecutionContextModel, ctx.execution_id)
            model.state = ExecutionState.RESUMING
            model.worker_name = "w2"
            model.park_deadline = datetime.now(timezone.utc) - timedelta(hours=3)
            session.commit()

        with patch("flux.config.Configuration.get") as cfg:
            cfg.return_value.settings.workers.park_ttl = 60
            cm.release_worker(ctx.execution_id)

        _, _, deadline = self._row(ctx.execution_id)
        assert deadline > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        assert cm.fail_expired_parked() == []

    def test_expired_bound_resume_is_failed_with_a_diagnostic(self, clean_env):
        cm, registry = clean_env
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")
        _force_state(ctx.execution_id, ExecutionState.RESUMING, worker_name=None)
        self._expire(ctx.execution_id)

        assert cm.fail_expired_parked() == [ctx.execution_id]

        loaded = cm.get(ctx.execution_id)
        assert loaded.has_failed
        assert "w2" in str(loaded.output)
        assert "resumed" in str(loaded.output)

    def test_unbound_resuming_is_never_swept(self, clean_env):
        """Any worker can take an unbound resume, so it is not stuck and must
        keep the pre-existing behaviour of waiting."""
        cm, registry = clean_env
        _register_worker(registry, "w1")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        _force_state(ctx.execution_id, ExecutionState.RESUMING, worker_name=None)
        self._expire(ctx.execution_id)

        assert cm.fail_expired_parked() == []

    def test_assigned_bound_resume_is_never_swept(self, clean_env):
        """Still assigned means still dispatchable to its worker; only the
        released (unassigned) case is stuck."""
        cm, registry = clean_env
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")
        _force_state(ctx.execution_id, ExecutionState.RESUMING, worker_name="w2")
        self._expire(ctx.execution_id)

        assert cm.fail_expired_parked() == []

    def test_unclaim_starts_the_clock_for_a_bound_execution(self, clean_env):
        """The other route into bound-and-unassigned RESUMING: a worker that
        crashed while resuming gets reaped through unclaim()."""
        cm, registry = clean_env
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")
        _force_state(ctx.execution_id, ExecutionState.RESUME_SCHEDULED, worker_name="w2")

        with patch("flux.config.Configuration.get") as cfg:
            cfg.return_value.settings.workers.park_ttl = 60
            cm.unclaim(ctx.execution_id)

        state, worker_name, deadline = self._row(ctx.execution_id)
        assert (state, worker_name) == (ExecutionState.RESUMING, None)
        assert deadline is not None

    def test_wake_restarts_the_clock_for_a_released_bound_execution(self, clean_env):
        from datetime import datetime, timedelta, timezone

        cm, registry = clean_env
        _register_worker(registry, "w2")
        wf_id = _create_workflow("plain")
        ctx = _create_execution(cm, wf_id, name="plain")
        self._bind(ctx.execution_id, "w2")

        # Paused, released by eviction, and long past the original deadline.
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            model = session.get(ExecutionContextModel, ctx.execution_id)
            model.state = ExecutionState.PAUSED
            model.worker_name = None
            model.park_deadline = datetime.now(timezone.utc) - timedelta(hours=3)
            session.commit()

        loaded = cm.get(ctx.execution_id)
        loaded.start_resuming()
        with patch("flux.config.Configuration.get") as cfg:
            cfg.return_value.settings.workers.park_ttl = 60
            cm.save(loaded)

        state, worker_name, deadline = self._row(ctx.execution_id)
        assert (state, worker_name) == (ExecutionState.RESUMING, None)
        assert deadline > datetime.now(timezone.utc).replace(tzinfo=None)
        assert cm.fail_expired_parked() == []
