"""Tests for the workflow module loader: source-hash keys and the LRU bound."""

from __future__ import annotations

import base64
import sys

import pytest

from flux.runners.loader import WorkflowModuleLoader


def b64(source_text: str) -> str:
    return base64.b64encode(source_text.encode()).decode()


def test_lru_evicts_oldest_module_and_its_sys_modules_entry():
    loader = WorkflowModuleLoader(max_size=2)

    loader.load("default", "wf_a", 1, b64("x = 1"))
    loader.load("default", "wf_b", 1, b64("x = 2"))
    first_key, second_key = list(loader._cache)
    evicted_name = loader._cache[first_key][0].__name__
    survivor_name = loader._cache[second_key][0].__name__
    assert evicted_name in sys.modules

    loader.load("default", "wf_c", 1, b64("x = 3"))

    assert len(loader._cache) == 2
    assert first_key not in loader._cache
    assert evicted_name not in sys.modules
    assert survivor_name in sys.modules


def test_cache_hit_refreshes_lru_position():
    loader = WorkflowModuleLoader(max_size=2)

    loader.load("default", "wf_a", 1, b64("x = 1"))
    loader.load("default", "wf_b", 1, b64("x = 2"))
    key_a, key_b = list(loader._cache)

    # Hit wf_a: it becomes most-recently-used, so wf_b is the eviction victim.
    loader.load("default", "wf_a", 1, b64("x = 1"))
    loader.load("default", "wf_c", 1, b64("x = 3"))

    assert key_a in loader._cache
    assert key_b not in loader._cache


def test_cache_hit_returns_same_module():
    loader = WorkflowModuleLoader(max_size=8)

    first = loader.load("default", "wf_a", 1, b64("x = 1"))
    second = loader.load("default", "wf_a", 1, b64("x = 1"))
    assert first is second


def test_same_version_source_change_recompiles_immediately():
    """Re-registered source under the same version must not serve stale code."""
    loader = WorkflowModuleLoader(max_size=8)

    old = loader.load("default", "wf_a", 1, b64("marker = 'old'"))
    new = loader.load("default", "wf_a", 1, b64("marker = 'new'"))

    assert len(loader._cache) == 2
    assert old.marker == "old"
    assert new.marker == "new"
    # Each source variant owns its own sys.modules entry.
    assert old.__name__ != new.__name__


def test_unbounded_when_max_size_zero():
    """max_size=0 keeps the legacy unbounded behavior."""
    loader = WorkflowModuleLoader(max_size=0)

    for i in range(5):
        loader.load("default", f"wf_{i}", 1, b64(f"x = {i}"))

    assert len(loader._cache) == 5


def test_ttl_zero_disables_caching():
    loader = WorkflowModuleLoader(ttl=0)

    first = loader.load("default", "wf_a", 1, b64("x = 1"))
    second = loader.load("default", "wf_a", 1, b64("x = 1"))
    assert first is not second
    assert len(loader._cache) == 0


@pytest.mark.asyncio
async def test_inprocess_runner_executes_workflow():
    """The in-process runner compiles, finds, and runs the workflow."""
    from flux.domain.execution_context import ExecutionContext
    from flux.runners.base import RunnerHooks
    from flux.runners.inprocess import InProcessRunner
    from flux.worker import WorkflowDefinition, WorkflowExecutionRequest

    source = """
from flux import ExecutionContext, workflow


@workflow
async def loader_wf(ctx: ExecutionContext[int]):
    return ctx.input + 1
"""
    checkpoints = []

    async def checkpoint(ctx):
        checkpoints.append(ctx.state.value)

    async def resolver(names):
        return {}

    request = WorkflowExecutionRequest(
        workflow=WorkflowDefinition(
            id="default/loader_wf",
            namespace="default",
            name="loader_wf",
            version=1,
            source=b64(source),
        ),
        context=ExecutionContext(
            workflow_id="default/loader_wf",
            workflow_namespace="default",
            workflow_name="loader_wf",
            input=41,
        ),
    )
    runner = InProcessRunner(loader=WorkflowModuleLoader(ttl=0))
    hooks = RunnerHooks(checkpoint=checkpoint, get_secrets=resolver, get_configs=resolver)
    ctx = await runner.execute(request, hooks)

    assert ctx.has_finished and not ctx.has_failed
    assert ctx.output == 42
