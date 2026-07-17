from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.context_managers import DatabaseContextManager
from flux.domain import ExecutionState
from flux.domain.execution_context import ExecutionContext
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
    return registry.register(
        name=name,
        runtime=_make_runtime(),
        packages=[],
        resources=_make_resources(),
        labels=labels,
    )


def _create_workflow(cm, name, namespace="default", affinity=None, requests=None):
    from flux.models import WorkflowModel, RepositoryFactory

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


def _create_execution(cm, workflow_id, namespace="default", name="test", input=None):
    ctx = ExecutionContext(
        workflow_id=workflow_id,
        workflow_namespace=namespace,
        workflow_name=name,
        input=input,
    )
    return cm.save(ctx)


def test_dispatch_matches_worker_with_correct_labels(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"})
    _create_execution(cm, wf_id, name="agent")

    result = cm.next_execution(w1)
    assert result is not None
    assert result.workflow_name == "agent"


def test_dispatch_skips_worker_without_matching_labels(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "compute"})
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"})
    _create_execution(cm, wf_id, name="agent")

    result = cm.next_execution(w1)
    assert result is None


def test_dispatch_worker_no_labels_gets_no_affinity_workflow(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1")
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "simple")
    _create_execution(cm, wf_id, name="simple")

    result = cm.next_execution(w1)
    assert result is not None
    assert result.workflow_name == "simple"


def test_dispatch_worker_no_labels_skips_affinity_workflow(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1")
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"})
    _create_execution(cm, wf_id, name="agent")

    result = cm.next_execution(w1)
    assert result is None


def test_dispatch_with_both_affinity_and_requests(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    requests_dict = {"cpu": 2, "memory": "1Gi"}
    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"}, requests=requests_dict)
    _create_execution(cm, wf_id, name="agent")

    result = cm.next_execution(w1)
    assert result is not None


def test_dispatch_constrained_before_unconstrained(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    wf_simple_id = _create_workflow(cm, "simple")
    _create_execution(cm, wf_simple_id, name="simple")

    wf_agent_id = _create_workflow(cm, "agent", affinity={"role": "harness"})
    _create_execution(cm, wf_agent_id, name="agent")

    result = cm.next_execution(w1)
    assert result is not None
    assert result.workflow_name == "agent"


def test_resume_prefers_original_worker(clean_env):
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    _register_worker(registry, "w2", labels={"role": "harness"})
    w1 = registry.get("w1")
    w2 = registry.get("w2")

    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"})
    ctx = _create_execution(cm, wf_id, name="agent")

    cm.claim(ctx.execution_id, w1)
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.RESUMING
        session.commit()

    result = cm.next_resume(w1)
    assert result is not None
    assert result.execution_id == ctx.execution_id

    result2 = cm.next_resume(w2)
    assert result2 is None


def test_resume_falls_back_to_label_match(clean_env):
    """Realistic scenario: w1 runs, pauses, gets evicted (unclaim clears worker_name),
    resume is called, w2 picks it up via affinity label match."""
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    _register_worker(registry, "w2", labels={"role": "harness"})
    w1 = registry.get("w1")
    w2 = registry.get("w2")

    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"})
    ctx = _create_execution(cm, wf_id, name="agent")

    # w1 claims and runs the execution, then it pauses
    cm.claim(ctx.execution_id, w1)
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.PAUSED
        session.commit()

    # w1 goes offline — eviction calls unclaim (no-op for PAUSED), then release_worker
    cm.unclaim(ctx.execution_id)  # returns unchanged (PAUSED not reclaimable)
    cm.release_worker(ctx.execution_id)  # clears worker_name, keeps PAUSED

    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        assert model.state == ExecutionState.PAUSED
        assert model.worker_name is None

    # Resume is called — transitions to RESUMING with worker_name still NULL
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.RESUMING
        session.commit()

    # w2 picks it up via affinity fallback
    result = cm.next_resume(w2)
    assert result is not None
    assert result.execution_id == ctx.execution_id


def test_resume_fallback_rejects_affinity_mismatch(clean_env):
    """Resume fallback rejects workers that don't match affinity."""
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    _register_worker(registry, "w2", labels={"role": "compute"})
    w1 = registry.get("w1")
    w2 = registry.get("w2")

    wf_id = _create_workflow(cm, "agent2", affinity={"role": "harness"})
    ctx = _create_execution(cm, wf_id, name="agent2")

    # w1 claimed, paused, then evicted
    cm.claim(ctx.execution_id, w1)
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.PAUSED
        session.commit()

    cm.unclaim(ctx.execution_id)
    cm.release_worker(ctx.execution_id)

    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.RESUMING
        session.commit()

    # w2 has wrong labels — should not get the execution
    result = cm.next_resume(w2)
    assert result is None


def test_release_worker_paused_clears_worker_name(clean_env):
    """release_worker on PAUSED clears worker_name but preserves state."""
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "agent3", affinity={"role": "harness"})
    ctx = _create_execution(cm, wf_id, name="agent3")

    cm.claim(ctx.execution_id, w1)
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.PAUSED
        session.commit()

    result = cm.release_worker(ctx.execution_id)
    assert result.state == ExecutionState.PAUSED
    assert result.current_worker == ""


def test_release_worker_resuming_clears_worker_name(clean_env):
    """release_worker on RESUMING clears worker_name but preserves state."""
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "agent4", affinity={"role": "harness"})
    ctx = _create_execution(cm, wf_id, name="agent4")

    cm.claim(ctx.execution_id, w1)
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.RESUMING
        session.commit()

    result = cm.release_worker(ctx.execution_id)
    assert result.state == ExecutionState.RESUMING
    assert result.current_worker == ""


def test_release_worker_noop_on_running(clean_env):
    """release_worker is a no-op on RUNNING executions (not releasable)."""
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    wf_id = _create_workflow(cm, "agent5", affinity={"role": "harness"})
    ctx = _create_execution(cm, wf_id, name="agent5")

    cm.claim(ctx.execution_id, w1)
    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.RUNNING
        session.commit()

    result = cm.release_worker(ctx.execution_id)
    assert result.current_worker == "w1"


def test_dispatch_affinity_matches_but_resources_insufficient(clean_env):
    """Worker matches affinity labels but fails resource requirements."""
    cm, registry = clean_env
    _register_worker(registry, "w1", labels={"role": "harness"})
    w1 = registry.get("w1")

    requests_dict = {"cpu": 128}
    wf_id = _create_workflow(cm, "agent", affinity={"role": "harness"}, requests=requests_dict)
    _create_execution(cm, wf_id, name="agent")

    result = cm.next_execution(w1)
    assert result is None


# ---------------------------------------------------------------------------
# require(...) affinity expressions (poll-mode dispatch)
# ---------------------------------------------------------------------------


def _require_spec(*terms):
    from flux.routing import require

    return require(*terms)


def test_poll_require_input_driven_match(clean_env):
    from flux.routing import input as input_, label

    cm, registry = clean_env
    _register_worker(registry, "eu", labels={"region": "eu-west"})
    _register_worker(registry, "us", labels={"region": "us-east"})
    eu, us = registry.get("eu"), registry.get("us")

    wf_id = _create_workflow(
        cm,
        "infer",
        affinity=_require_spec(label("region") == input_("region")),
    )
    _create_execution(cm, wf_id, name="infer", input={"region": "eu-west"})

    assert cm.next_execution(us) is None
    result = cm.next_execution(eu)
    assert result is not None and result.workflow_name == "infer"


def test_poll_require_unresolved_input_fails_execution(clean_env):
    from flux.routing import input as input_, label

    cm, registry = clean_env
    _register_worker(registry, "eu", labels={"region": "eu-west"})
    eu = registry.get("eu")

    wf_id = _create_workflow(
        cm,
        "infer",
        affinity=_require_spec(label("region") == input_("region")),
    )
    ctx = _create_execution(cm, wf_id, name="infer", input={"other": 1})

    assert cm.next_execution(eu) is None

    failed = cm.get(ctx.execution_id)
    assert failed.state == ExecutionState.FAILED
    [event] = [e for e in failed.events if e.type.name == "WORKFLOW_FAILED"]
    assert "region" in str(event.value)


def test_poll_require_mismatch_parks_execution(clean_env):
    """A resolvable-but-unmatched expression waits for a matching worker."""
    from flux.routing import input as input_, label

    cm, registry = clean_env
    _register_worker(registry, "us", labels={"region": "us-east"})
    us = registry.get("us")

    wf_id = _create_workflow(
        cm,
        "infer",
        affinity=_require_spec(label("region") == input_("region")),
    )
    ctx = _create_execution(cm, wf_id, name="infer", input={"region": "eu-west"})

    assert cm.next_execution(us) is None
    assert cm.get(ctx.execution_id).state == ExecutionState.CREATED

    _register_worker(registry, "eu", labels={"region": "eu-west"})
    assert cm.next_execution(registry.get("eu")) is not None


def test_poll_require_dynamic_key_and_optional_pin(clean_env):
    from flux.routing import input as input_, label, label_for, optional

    cm, registry = clean_env
    _register_worker(registry, "a", labels={"sku.small-8b": "true", "node": "node_a"})
    _register_worker(registry, "b", labels={"sku.small-8b": "true", "node": "node_b"})
    a, b = registry.get("a"), registry.get("b")

    wf_id = _create_workflow(
        cm,
        "infer",
        affinity=_require_spec(
            label_for("sku.", input_("model")) == "true",
            optional(label("node") == input_("node")),
        ),
    )
    _create_execution(
        cm,
        wf_id,
        name="infer",
        input={"model": "small-8b", "node": "node_b"},
    )

    assert cm.next_execution(a) is None
    assert cm.next_execution(b) is not None


def test_resume_fallback_respects_require_expression(clean_env):
    from flux.routing import input as input_, label

    cm, registry = clean_env
    _register_worker(registry, "us", labels={"region": "us-east"})
    _register_worker(registry, "eu", labels={"region": "eu-west"})
    us, eu = registry.get("us"), registry.get("eu")

    wf_id = _create_workflow(
        cm,
        "infer",
        affinity=_require_spec(label("region") == input_("region")),
    )
    ctx = _create_execution(cm, wf_id, name="infer", input={"region": "eu-west"})

    from flux.models import ExecutionContextModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        model = session.get(ExecutionContextModel, ctx.execution_id)
        model.state = ExecutionState.RESUMING
        model.worker_name = None
        session.commit()

    assert cm.next_resume(us) is None
    result = cm.next_resume(eu)
    assert result is not None and result.workflow_name == "infer"
