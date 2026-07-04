"""Tests for the runner-pinning example.

Runner selection is a dispatch concern; inline execution runs the workflow
in the current process regardless of the annotation, so these tests verify
the workflows themselves and that each carries its runner requirement.
"""

from __future__ import annotations

from examples.runners import containerized, fast_hop, isolated_by_default


def test_fast_hop_runs_inline():
    ctx = fast_hop.run(21)
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == 42


def test_isolated_by_default_runs_inline():
    ctx = isolated_by_default.run(21)
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == 42


def test_containerized_runs_inline():
    ctx = containerized.run(21)
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == 42


def test_examples_declare_their_runners():
    assert fast_hop.runner == "inprocess"
    assert isolated_by_default.runner == "subprocess"
    assert containerized.runner == "docker"
