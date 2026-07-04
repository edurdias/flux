"""Tests for the same-worker transient fast path in call().

A sync call() whose target is a transient workflow object executes
in-process — no server, no execution row. Everything else (durable
targets, string references, async mode, fast path disabled) relays
through the server; these tests assert the relay is attempted by pointing
the server URL at a closed port and expecting the connect error.
"""

from __future__ import annotations

import pytest

from flux import ExecutionContext
from flux.config import Configuration
from flux.task import task
from flux.tasks.call import call
from flux.workflow import workflow


@task
async def double(x: int) -> int:
    return x * 2


@workflow.with_options(durability="transient")
async def transient_child(ctx: ExecutionContext[int]):
    return await double(ctx.input)


@workflow.with_options(durability="transient")
async def transient_failing(ctx: ExecutionContext):
    raise ValueError("hop failed")


@workflow
async def durable_child(ctx: ExecutionContext[int]):
    return await double(ctx.input)


@workflow.with_options(durability="transient", runner="subprocess")
async def transient_isolated(ctx: ExecutionContext[int]):
    return await double(ctx.input)


@pytest.fixture
def unreachable_server():
    """Point the relay path at a port nothing listens on."""
    config = Configuration.get()
    config.override(workers={"server_url": "http://127.0.0.1:1"})
    yield
    config.reset()


@workflow
async def parent_calls_transient(ctx: ExecutionContext[int]):
    return await call(transient_child, ctx.input)


@workflow
async def parent_calls_failing(ctx: ExecutionContext):
    return await call(transient_failing, None)


@workflow
async def parent_calls_durable(ctx: ExecutionContext[int]):
    return await call(durable_child, ctx.input)


@workflow
async def parent_calls_transient_async(ctx: ExecutionContext[int]):
    return await call(transient_child, ctx.input, mode="async")


def test_transient_object_executes_in_process(unreachable_server):
    """No server anywhere: the fast path must not touch the network."""
    ctx = parent_calls_transient.run(21)
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == 42


def test_transient_hop_failure_propagates(unreachable_server):
    ctx = parent_calls_failing.run(None)
    assert ctx.has_failed
    assert "hop failed" in str(ctx.output)


def test_durable_object_still_relays(unreachable_server):
    """Durable targets need the server lifecycle — the relay is attempted."""
    ctx = parent_calls_durable.run(21)
    assert ctx.has_failed
    assert "Could not connect" in str(ctx.output)


def test_async_mode_still_relays(unreachable_server):
    """mode=async returns an execution_id others can query — server only."""
    ctx = parent_calls_transient_async.run(21)
    assert ctx.has_failed
    assert "Could not connect" in str(ctx.output)


def test_declared_runner_requirement_disables_fast_path(unreachable_server):
    """A transient target pinned to an isolating runner must not run in the
    caller's process — it relays so dispatch honors the runner constraint."""

    @workflow
    async def parent_calls_isolated(ctx: ExecutionContext[int]):
        return await call(transient_isolated, ctx.input)

    ctx = parent_calls_isolated.run(21)
    assert ctx.has_failed
    assert "Could not connect" in str(ctx.output)


def test_fast_path_can_be_disabled(unreachable_server):
    config = Configuration.get()
    config.override(workers={"server_url": "http://127.0.0.1:1", "transient_fast_path": False})
    try:
        ctx = parent_calls_transient.run(21)
        assert ctx.has_failed
        assert "Could not connect" in str(ctx.output)
    finally:
        config.reset()


def test_nested_context_is_restored_after_hop(unreachable_server):
    """The parent's context is current again after the in-process hop."""

    @workflow
    async def parent_checks_ctx(ctx: ExecutionContext[int]):
        result = await call(transient_child, ctx.input)
        current = await ExecutionContext.get()
        assert current.execution_id == ctx.execution_id
        return result

    ctx = parent_checks_ctx.run(21)
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == 42
