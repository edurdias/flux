"""Tests for Graph cycle detection and conditional-edge evaluation."""

from __future__ import annotations

import pytest

from flux import ExecutionContext
from flux.task import task
from flux.tasks import Graph
from flux.workflow import workflow


@task
async def _passthrough(value=None):
    return value


@task
async def _double(value) -> int:
    return value * 2


async def _always(_value) -> bool:
    return True


async def _never(_value) -> bool:
    return False


def test_validate_detects_cycle():
    g = Graph("cyclic")
    g.add_node("a", _passthrough)
    g.add_node("b", _passthrough)
    g.add_edge("a", "b")
    g.add_edge("b", "a")
    g.start_with("a")
    g.end_with("b")

    with pytest.raises(ValueError, match="cycle"):
        g.validate()


def test_validate_accepts_acyclic_graph():
    g = Graph("acyclic")
    g.add_node("a", _passthrough)
    g.add_node("b", _passthrough)
    g.add_edge("a", "b")
    g.start_with("a")
    g.end_with("b")

    assert g.validate() is g


@workflow
async def _gated_true_wf(ctx: ExecutionContext[int]):
    g = (
        Graph("gated_true")
        .add_node("a", _passthrough)
        .add_node("b", _double)
        .add_edge("a", "b", _always)
        .start_with("a")
        .end_with("b")
    )
    return await g(ctx.input)


@workflow
async def _gated_false_wf(ctx: ExecutionContext[int]):
    g = (
        Graph("gated_false")
        .add_node("a", _passthrough)
        .add_node("b", _double)
        .add_edge("a", "b", _never)
        .start_with("a")
        .end_with("b")
    )
    return await g(ctx.input)


def test_conditional_edge_true_runs_downstream():
    ctx = _gated_true_wf.run(7)
    assert ctx.has_finished and ctx.has_succeeded
    ran = any("_double" in str(e.name) for e in ctx.events)
    assert ran, "downstream node should run when the edge condition is true"
    assert ctx.output == 14


def test_conditional_edge_false_skips_downstream():
    # __can_execute is async; before the fix it was never awaited, so the
    # condition coroutine was always truthy and the downstream node ran
    # regardless. A false condition must now prevent it.
    ctx = _gated_false_wf.run(7)
    assert ctx.has_finished and ctx.has_succeeded
    ran = any("_double" in str(e.name) for e in ctx.events)
    assert not ran, "downstream node ran despite a false edge condition"
