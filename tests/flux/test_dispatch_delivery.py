"""Guards for how a batch of assignments reaches the worker queues.

Delivery used to await each execution in turn, so a batch cost the sum of
its payload builds and the last frame waited behind every earlier one. That
is one of the terms making claim latency grow with concurrency (#287).
Builds now run concurrently; enqueueing stays ordered.
"""

from __future__ import annotations

import asyncio

import pytest

from flux.dispatcher import Dispatcher


class _Ctx:
    def __init__(self, execution_id: str):
        self.execution_id = execution_id


class _FakeServer:
    """Only the surface _deliver touches."""

    def __init__(self, worker_names, build_delay=0.0):
        self._worker_queues = {name: asyncio.Queue() for name in worker_names}
        self._work_available = asyncio.Event()
        self._build_delay = build_delay
        self.build_starts: list[str] = []
        self.concurrent_builds = 0
        self.peak_concurrent_builds = 0

    def _build_dispatch_payload(self, ctx):
        # Called in a worker thread; the counters are only touched here and
        # read after the gather completes.
        self.concurrent_builds += 1
        self.peak_concurrent_builds = max(
            self.peak_concurrent_builds,
            self.concurrent_builds,
        )
        self.build_starts.append(ctx.execution_id)
        if self._build_delay:
            import time

            time.sleep(self._build_delay)
        self.concurrent_builds -= 1
        return {"execution_id": ctx.execution_id}


class _FakeManager:
    def __init__(self):
        self.unclaimed: list[str] = []

    def unclaim(self, execution_id):
        self.unclaimed.append(execution_id)


def _dispatcher(server) -> Dispatcher:
    d = Dispatcher.__new__(Dispatcher)
    d._server = server
    return d


def _drain(queue: asyncio.Queue) -> list[str]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait().execution_id)
    return out


@pytest.mark.asyncio
async def test_batch_frames_keep_dispatch_order():
    """Parallel builds must not reorder what the worker sees."""
    server = _FakeServer(["w1"])
    dispatcher = _dispatcher(server)
    assignments = [(_Ctx(f"e{i}"), "w1") for i in range(8)]

    await dispatcher._deliver_all(_FakeManager(), assignments, "execution_scheduled")

    assert _drain(server._worker_queues["w1"]) == [f"e{i}" for i in range(8)]


@pytest.mark.asyncio
async def test_batch_builds_payloads_concurrently():
    """The regression this fixes: N builds costing N times one build."""
    server = _FakeServer(["w1"], build_delay=0.05)
    dispatcher = _dispatcher(server)
    assignments = [(_Ctx(f"e{i}"), "w1") for i in range(8)]

    await dispatcher._deliver_all(_FakeManager(), assignments, "execution_scheduled")

    # Serialized, eight 50 ms builds take 400 ms. The assertion is on
    # observed overlap rather than wall clock, so a slow machine cannot
    # make it flake.
    assert server.peak_concurrent_builds > 1
    assert len(server.build_starts) == 8


@pytest.mark.asyncio
async def test_single_assignment_takes_the_direct_path():
    server = _FakeServer(["w1"])
    dispatcher = _dispatcher(server)

    await dispatcher._deliver_all(
        _FakeManager(),
        [(_Ctx("only"), "w1")],
        "execution_scheduled",
    )

    assert _drain(server._worker_queues["w1"]) == ["only"]


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop():
    server = _FakeServer(["w1"])
    dispatcher = _dispatcher(server)

    await dispatcher._deliver_all(_FakeManager(), [], "execution_scheduled")

    assert server.build_starts == []


@pytest.mark.asyncio
async def test_a_failed_build_releases_only_its_own_execution():
    """One bad payload must not take the rest of the batch down with it."""

    class _PickyServer(_FakeServer):
        def _build_dispatch_payload(self, ctx):
            if ctx.execution_id == "e1":
                raise RuntimeError("catalog read failed")
            return super()._build_dispatch_payload(ctx)

    server = _PickyServer(["w1"])
    dispatcher = _dispatcher(server)
    manager = _FakeManager()
    assignments = [(_Ctx(f"e{i}"), "w1") for i in range(3)]

    await dispatcher._deliver_all(manager, assignments, "execution_scheduled")

    assert _drain(server._worker_queues["w1"]) == ["e0", "e2"]
    assert manager.unclaimed == ["e1"]


@pytest.mark.asyncio
async def test_delivery_to_a_vanished_worker_releases_the_execution():
    server = _FakeServer([])  # the worker disconnected before delivery
    dispatcher = _dispatcher(server)
    manager = _FakeManager()

    await dispatcher._deliver_all(
        manager,
        [(_Ctx("e0"), "gone"), (_Ctx("e1"), "gone")],
        "execution_scheduled",
    )

    assert manager.unclaimed == ["e0", "e1"]
