"""Tests for claim-generation fencing (split-brain prevention)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.context_managers import DatabaseContextManager
from flux.domain.execution_context import ExecutionContext
from flux.errors import StaleClaimError
from flux.worker_registry import (
    DatabaseWorkerRegistry,
    WorkerResourcesInfo,
    WorkerRuntimeInfo,
)


@pytest.fixture
def env():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield DatabaseContextManager(), DatabaseWorkerRegistry()
    if os.path.exists(db_path):
        os.unlink(db_path)


def _register(registry, name):
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


def _create_workflow(cm):
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id="default/wf",
                name="wf",
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace="default",
            ),
        )
        session.commit()
    return "default/wf"


def _dispatch(cm, worker):
    ctx = cm.next_execution(worker)
    assert ctx is not None
    return cm.claim(ctx.execution_id, worker)


def test_generation_starts_at_zero_and_bumps_on_dispatch(env):
    cm, registry = env
    w1 = _register(registry, "w1")
    wf_id = _create_workflow(cm)
    ctx = cm.save(
        ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name="wf"),
    )

    assert cm.get_claim_generation(ctx.execution_id) == 0
    _dispatch(cm, w1)
    assert cm.get_claim_generation(ctx.execution_id) == 1


def test_generation_bumps_again_on_reassignment(env):
    cm, registry = env
    w1 = _register(registry, "w1")
    wf_id = _create_workflow(cm)
    saved = cm.save(
        ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name="wf"),
    )

    _dispatch(cm, w1)
    cm.unclaim(saved.execution_id)  # eviction path: back to CREATED
    _dispatch(cm, w1)

    assert cm.get_claim_generation(saved.execution_id) == 2


def test_update_rejects_stale_generation(env):
    cm, registry = env
    w1 = _register(registry, "w1")
    wf_id = _create_workflow(cm)
    cm.save(ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name="wf"))
    ctx = _dispatch(cm, w1)  # generation is now 1

    # A checkpoint claimed under generation 0 (pre-reassignment) is fenced.
    with pytest.raises(StaleClaimError):
        cm.update(ctx, expected_claim_generation=0)

    # The current generation passes.
    cm.update(ctx, expected_claim_generation=1)


def test_update_without_generation_is_unfenced(env):
    """Legacy workers send no generation header and keep working."""
    cm, registry = env
    w1 = _register(registry, "w1")
    wf_id = _create_workflow(cm)
    cm.save(ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name="wf"))
    ctx = _dispatch(cm, w1)

    cm.update(ctx)  # no expected generation: accepted


def test_batch_dispatch_bumps_generation(env):
    cm, registry = env
    w1 = _register(registry, "w1")
    wf_id = _create_workflow(cm)
    saved = cm.save(
        ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name="wf"),
    )

    assignments = cm.next_executions_batch([w1], limit=10)

    assert len(assignments) == 1
    assert cm.get_claim_generation(saved.execution_id) == 1
