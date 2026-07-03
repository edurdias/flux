"""Tests for the worker module cache: source-hash keys and the LRU bound."""

from __future__ import annotations

import base64
import sys
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.errors import WorkflowNotFoundError
from flux.worker import Worker, WorkflowDefinition, WorkflowExecutionRequest


def make_cache_worker(max_size: int = 2, ttl: int = 300) -> Worker:
    worker = Worker.__new__(Worker)
    worker._module_cache = OrderedDict()
    worker._module_cache_ttl = ttl
    worker._module_cache_max_size = max_size
    worker._running_workflows = {}
    worker._setup_progress = MagicMock()  # type: ignore[method-assign]
    worker._teardown_progress = AsyncMock()  # type: ignore[method-assign]
    return worker


def make_request(name: str, source_text: str, version: int = 1) -> WorkflowExecutionRequest:
    return WorkflowExecutionRequest(
        workflow=WorkflowDefinition(
            id=f"default/{name}",
            namespace="default",
            name=name,
            version=version,
            source=base64.b64encode(source_text.encode()).decode(),
        ),
        context=ExecutionContext(
            workflow_id=f"default/{name}",
            workflow_namespace="default",
            workflow_name=name,
        ),
    )


async def compile_only(worker: Worker, request: WorkflowExecutionRequest) -> None:
    """Run _run_workflow far enough to compile + cache the module.

    The sources under test deliberately define no workflow object, so the
    call raises WorkflowNotFoundError after the caching section — which is
    all these tests exercise.
    """
    with pytest.raises(WorkflowNotFoundError):
        await worker._run_workflow(request)


@pytest.mark.asyncio
async def test_lru_evicts_oldest_module_and_its_sys_modules_entry():
    worker = make_cache_worker(max_size=2)

    await compile_only(worker, make_request("wf_a", "x = 1"))
    await compile_only(worker, make_request("wf_b", "x = 2"))
    first_key, second_key = list(worker._module_cache)
    evicted_name = worker._module_cache[first_key][0].__name__
    survivor_name = worker._module_cache[second_key][0].__name__
    assert evicted_name in sys.modules

    await compile_only(worker, make_request("wf_c", "x = 3"))

    assert len(worker._module_cache) == 2
    assert first_key not in worker._module_cache
    assert evicted_name not in sys.modules
    assert survivor_name in sys.modules


@pytest.mark.asyncio
async def test_cache_hit_refreshes_lru_position():
    worker = make_cache_worker(max_size=2)

    await compile_only(worker, make_request("wf_a", "x = 1"))
    await compile_only(worker, make_request("wf_b", "x = 2"))
    key_a, key_b = list(worker._module_cache)

    # Hit wf_a: it becomes most-recently-used, so wf_b is the eviction victim.
    await compile_only(worker, make_request("wf_a", "x = 1"))
    await compile_only(worker, make_request("wf_c", "x = 3"))

    assert key_a in worker._module_cache
    assert key_b not in worker._module_cache


@pytest.mark.asyncio
async def test_same_version_source_change_recompiles_immediately():
    """Re-registered source under the same version must not serve stale code."""
    worker = make_cache_worker(max_size=8)

    await compile_only(worker, make_request("wf_a", "marker = 'old'"))
    await compile_only(worker, make_request("wf_a", "marker = 'new'"))

    assert len(worker._module_cache) == 2
    markers = {mod.marker for mod, _ in worker._module_cache.values()}
    assert markers == {"old", "new"}


@pytest.mark.asyncio
async def test_unbounded_when_max_size_zero():
    """max_size=0 keeps the legacy unbounded behavior."""
    worker = make_cache_worker(max_size=0)

    for i in range(5):
        await compile_only(worker, make_request(f"wf_{i}", f"x = {i}"))

    assert len(worker._module_cache) == 5
