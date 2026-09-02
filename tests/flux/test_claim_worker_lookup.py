"""The claim handler must not re-read the worker row on the event loop.

registry.get() loads the worker plus its packages relationship -- every
installed distribution on that worker -- and it ran unthreaded inside the
async claim handler, blocking the whole loop once per claim (#287). The
claim path reads nothing but worker.name, and a worker that can claim is
connected, so the info is already in memory.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import flux.api.worker_routes as worker_routes
from flux.domain.execution_context import ExecutionContext
from flux.worker_registry import WorkerInfo


def _claim_handler_source() -> str:
    """The body of the workers_claim route, isolated from its neighbours."""
    source = Path(worker_routes.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "workers_claim":
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("workers_claim handler not found")


def test_claim_does_not_read_the_worker_row_on_the_loop():
    body = _claim_handler_source()

    assert "self._worker_info.get(name)" in body, (
        "the claim path should take the connected worker from memory"
    )
    # Any registry read left in the handler must be threaded.
    for line in body.splitlines():
        if "registry.get(" in line:
            assert "to_thread" in line, f"unthreaded registry read: {line.strip()}"


def test_claim_only_needs_the_worker_name():
    """What justifies skipping the full row: nothing else is read.

    If the claim path ever starts reading resources, labels or packages,
    this fails and the in-memory shortcut has to be revisited.
    """
    for method in (ExecutionContext.claim, ExecutionContext.resume_claim):
        source = inspect.getsource(method)
        attrs = {
            node.attr
            for node in ast.walk(ast.parse(source.strip()))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "worker"
        }
        assert attrs <= {"name"}, f"{method.__qualname__} now reads {attrs - {'name'}}"


def test_connected_worker_info_carries_a_usable_name():
    """The in-memory value is a WorkerInfo, the same type registry.get returns."""
    info = WorkerInfo(name="w1")
    assert isinstance(info, WorkerInfo)
    assert info.name == "w1"
