"""Tests for worker self-health: loop-lag detection and work refusal."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.flux.test_worker_checkpoint import make_worker


def _scheduled_event(execution_id: str = "exec-unhealthy") -> MagicMock:
    evt = MagicMock()
    evt.json.return_value = {
        "workflow": {
            "id": "wf-1",
            "namespace": "default",
            "name": "wf",
            "version": 1,
            "source": "",
        },
        "context": {
            "workflow_id": "wf-1",
            "workflow_namespace": "default",
            "workflow_name": "wf",
            "execution_id": execution_id,
            "input": None,
            "state": "SCHEDULED",
            "events": [],
        },
    }
    return evt


class TestLoopLagMonitor:
    @pytest.mark.asyncio
    async def test_three_breaches_trip_unhealthy_three_recoveries_heal(self):
        worker = make_worker()
        worker._loop_lag_threshold = 1.0
        worker._loop_lag_probe_interval = 0.001
        worker._send_pong = AsyncMock()

        # Each probe reads monotonic twice (start, after sleep): feed three
        # starved probes (lag 10s) then an unbounded stream of clean ones —
        # the monitor keeps probing until cancelled, so a finite list would
        # exhaust into StopIteration inside the task.
        import itertools

        lagged = [0.0, 10.0] * 3
        clean = itertools.cycle([0.0, 0.001])
        with patch("flux.worker.time") as mock_time:
            mock_time.monotonic.side_effect = itertools.chain(lagged, clean)
            monitor = asyncio.create_task(worker._monitor_loop_health())
            try:
                async with asyncio.timeout(10):
                    while worker._healthy:
                        await asyncio.sleep(0.005)
                    unhealthy_seen = not worker._healthy
                    while not worker._healthy:
                        await asyncio.sleep(0.005)
            finally:
                monitor.cancel()

        assert unhealthy_seen
        assert worker._healthy  # recovered after three clean probes
        worker._send_pong.assert_called()  # state changes pushed to server

    @pytest.mark.asyncio
    async def test_single_breach_does_not_trip(self):
        worker = make_worker()
        worker._loop_lag_threshold = 1.0
        worker._loop_lag_probe_interval = 0.001
        worker._send_pong = AsyncMock()

        import itertools

        one_breach_then_clean = itertools.chain(
            [0.0, 10.0],
            itertools.cycle([0.0, 0.001]),
        )
        with patch("flux.worker.time") as mock_time:
            mock_time.monotonic.side_effect = one_breach_then_clean
            monitor = asyncio.create_task(worker._monitor_loop_health())
            await asyncio.sleep(0.05)
            monitor.cancel()

        assert worker._healthy


class TestUnhealthyWorkRefusal:
    @pytest.mark.asyncio
    async def test_scheduled_dispatch_is_released_when_unhealthy(self):
        worker = make_worker()
        worker._healthy = False
        worker._release_claim = AsyncMock()
        worker._authorized_post = AsyncMock()

        await worker._handle_execution_scheduled(
            "http://localhost:19000/workers/x",
            _scheduled_event(),
        )

        worker._release_claim.assert_awaited_once_with("exec-unhealthy")
        worker._authorized_post.assert_not_called()  # never claimed

    @pytest.mark.asyncio
    async def test_resumed_dispatch_is_released_when_unhealthy(self):
        worker = make_worker()
        worker._healthy = False
        worker._release_claim = AsyncMock()
        worker._authorized_post = AsyncMock()

        await worker._handle_execution_resumed(_scheduled_event("exec-resume"))

        worker._release_claim.assert_awaited_once_with("exec-resume")
        worker._authorized_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_pong_carries_health_state(self):
        worker = make_worker()
        worker._healthy = False
        captured = {}

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            response = MagicMock()
            response.status_code = 200
            return response

        worker._authorized_post = capture_post

        await worker._send_pong()

        assert captured["json"] == {"healthy": False}


class TestDispatcherExclusion:
    def test_connected_workers_excludes_unhealthy(self):
        from flux.dispatcher import Dispatcher

        dispatcher = Dispatcher.__new__(Dispatcher)
        server = MagicMock()
        server._worker_info = {"w1": "info1", "w2": "info2"}
        server._worker_queues = {"w1": MagicMock(), "w2": MagicMock()}
        server._worker_unhealthy = {"w2"}
        dispatcher._server = server

        assert dispatcher._connected_workers() == ["info1"]
