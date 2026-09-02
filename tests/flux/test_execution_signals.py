"""Guards for ExecutionSignals' two progress-buffer semantics.

Stage 3 (#264) routed all five buffer-acquisition sites through one
``open_progress_buffer``, which replaces unconditionally. That was a
behavior change at the one site that used to preserve: attaching a second
SSE stream to a running execution orphaned the first, whose ``get()`` stays
parked on a queue the producer no longer writes to. The two semantics are
now named separately, and these tests hold them apart.
"""

from __future__ import annotations

import asyncio

import pytest

from flux.execution_signals import PROGRESS_BUFFER_MAX, ExecutionSignals


def test_ensure_preserves_the_incumbent_buffer():
    signals = ExecutionSignals()

    first = signals.ensure_progress_buffer("exec-1")
    second = signals.ensure_progress_buffer("exec-1")

    assert second is first


def test_ensure_does_not_orphan_a_parked_consumer():
    """The failure Copilot described, encoded as behavior.

    A second attach must not redirect the producer away from the queue the
    first stream is already waiting on.
    """
    signals = ExecutionSignals()
    stream_a_buffer = signals.ensure_progress_buffer("exec-1")

    signals.ensure_progress_buffer("exec-1")  # a second stream attaches

    # The producer resolves the buffer by execution id on every POST.
    signals.progress_buffer("exec-1").put_nowait("frame")

    assert stream_a_buffer.get_nowait() == "frame"


def test_open_replaces_so_a_new_run_starts_clean():
    """The launch-then-stream endpoints keep their replacing semantics."""
    signals = ExecutionSignals()

    stale = signals.open_progress_buffer("exec-1")
    stale.put_nowait("frame from the previous run")

    fresh = signals.open_progress_buffer("exec-1")

    assert fresh is not stale
    assert fresh.empty()
    assert signals.progress_buffer("exec-1") is fresh


@pytest.mark.parametrize("factory", ["open_progress_buffer", "ensure_progress_buffer"])
def test_both_buffers_are_bounded(factory):
    """Unbounded growth is the failure the bound exists to prevent."""
    signals = ExecutionSignals()

    buffer = getattr(signals, factory)("exec-1")

    assert buffer.maxsize == PROGRESS_BUFFER_MAX


def test_drop_releases_the_buffer():
    signals = ExecutionSignals()
    buffer = signals.ensure_progress_buffer("exec-1")

    assert signals.drop_progress_buffer("exec-1") is buffer
    assert signals.progress_buffer("exec-1") is None


def test_stream_attach_endpoint_preserves_an_existing_buffer():
    """The endpoint that attaches must not use the replacing variant.

    A source guard: the behavioral difference only shows with two
    concurrent SSE clients on one execution, which is expensive to stage
    here, and the regression was precisely a one-line call-site swap.
    """
    import inspect

    from flux.api.execution_routes import ExecutionRoutesMixin

    source = inspect.getsource(ExecutionRoutesMixin)
    attach = source[source.index('if mode == "stream"') :]
    attach = attach[: attach.index("EventSourceResponse")]

    assert "ensure_progress_buffer" in attach
    assert "open_progress_buffer" not in attach


def test_producer_and_docstring_agree_on_what_is_dropped():
    """The docstring claimed drop-oldest; the producer drops the newest."""
    import inspect

    from flux.api.worker_routes import WorkerRoutesMixin

    producer = inspect.getsource(WorkerRoutesMixin)
    assert "except asyncio.QueueFull" in producer

    doc = ExecutionSignals.open_progress_buffer.__doc__ or ""
    assert "drops the oldest" not in doc


@pytest.mark.asyncio
async def test_bounded_buffer_drops_rather_than_blocks():
    signals = ExecutionSignals()
    buffer = signals.ensure_progress_buffer("exec-1")
    for i in range(PROGRESS_BUFFER_MAX):
        buffer.put_nowait(i)

    with pytest.raises(asyncio.QueueFull):
        buffer.put_nowait("one too many")
