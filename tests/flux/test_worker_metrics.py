"""Tests for the worker-side metrics provider (collection + pong payload)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from flux.worker import Worker
from tests.flux.test_worker_checkpoint import make_worker


def sample_provider() -> dict[str, float]:
    """Referenced by dotted path in the loading tests."""
    return {"fitness": 0.5}


class TestProviderLoading:
    def test_loads_callable_by_dotted_path(self):
        provider = Worker._load_metrics_provider(
            "tests.flux.test_worker_metrics:sample_provider",
        )
        assert provider is sample_provider

    @pytest.mark.parametrize(
        "spec",
        [
            "not.a.real.module:collect",
            "tests.flux.test_worker_metrics:missing",
            "tests.flux.test_worker_metrics",  # no callable part
            "tests.flux.test_worker_metrics:pytest",  # module attr, not ours but callable... use non-callable
        ],
    )
    def test_bad_specs_disable_the_provider(self, spec):
        if spec.endswith(":pytest"):
            spec = "tests.flux.test_worker_metrics:__doc__"  # non-callable attribute
        assert Worker._load_metrics_provider(spec) is None

    def test_none_spec_is_disabled(self):
        assert Worker._load_metrics_provider(None) is None


class TestCollection:
    @pytest.mark.asyncio
    async def test_sync_provider_collected_and_validated(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 3}
        worker._metrics_interval = 60.0

        assert await worker._collect_metrics() == {"queue": 3.0}

    @pytest.mark.asyncio
    async def test_async_provider_supported(self):
        worker = make_worker()

        async def provider():
            return {"latency": 1.5}

        worker._metrics_provider = provider
        worker._metrics_interval = 60.0

        assert await worker._collect_metrics() == {"latency": 1.5}

    @pytest.mark.asyncio
    async def test_snapshot_cached_within_interval(self):
        worker = make_worker()
        provider = MagicMock(return_value={"queue": 1.0})
        worker._metrics_provider = provider
        worker._metrics_interval = 60.0

        await worker._collect_metrics()
        await worker._collect_metrics()

        provider.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_payload_keeps_previous_snapshot(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 1.0}
        worker._metrics_interval = 0.0001

        first = await worker._collect_metrics()
        assert first == {"queue": 1.0}

        worker._metrics_provider = lambda: {"queue": "busted"}
        await asyncio.sleep(0.001)
        assert await worker._collect_metrics() == {"queue": 1.0}

    @pytest.mark.asyncio
    async def test_provider_exception_keeps_previous_snapshot(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 2.0}
        worker._metrics_interval = 0.0001

        assert await worker._collect_metrics() == {"queue": 2.0}

        def boom():
            raise RuntimeError("collector down")

        worker._metrics_provider = boom
        await asyncio.sleep(0.001)
        assert await worker._collect_metrics() == {"queue": 2.0}

    @pytest.mark.asyncio
    async def test_no_provider_returns_none(self):
        worker = make_worker()
        worker._metrics_provider = None

        assert await worker._collect_metrics() is None


class TestPongPayload:
    @pytest.mark.asyncio
    async def test_pong_carries_metrics_when_available(self):
        worker = make_worker()
        worker._metrics_provider = lambda: {"queue": 4.0}
        worker._metrics_interval = 60.0
        captured = {}

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            response = MagicMock()
            response.status_code = 200
            return response

        worker._authorized_post = capture_post

        await worker._send_pong()

        assert captured["json"] == {"healthy": True, "metrics": {"queue": 4.0}}

    @pytest.mark.asyncio
    async def test_pong_omits_metrics_without_provider(self):
        worker = make_worker()
        worker._metrics_provider = None
        captured = {}

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            response = MagicMock()
            response.status_code = 200
            return response

        worker._authorized_post = capture_post

        await worker._send_pong()

        assert captured["json"] == {"healthy": True}


class TestBuiltinMetricsCollector:
    def _collector(self, **kwargs):
        from flux.worker_metrics import WorkerMetricsCollector

        return WorkerMetricsCollector(**kwargs)

    def test_snapshot_always_reports_running_and_system_gauges(self):
        snapshot = self._collector().snapshot(running=3)

        assert snapshot["flux.running_executions"] == 3.0
        assert "flux.cpu_percent" in snapshot
        assert "flux.memory_available_bytes" in snapshot
        # Aggregates stay absent until their source has data.
        assert "flux.failure_rate" not in snapshot
        assert "flux.loop_lag_p95_seconds" not in snapshot

    def test_slots_free_only_with_bounded_capacity(self):
        assert "flux.slots_free" not in self._collector().snapshot(running=1)
        bounded = self._collector(max_concurrent=8).snapshot(running=3)
        assert bounded["flux.slots_free"] == 5.0

    def test_loop_lag_latest_and_p95(self):
        collector = self._collector()
        for lag in (0.01,) * 99 + (2.0,):
            collector.record_loop_lag(lag)

        snapshot = collector.snapshot(running=0)

        assert snapshot["flux.loop_lag_seconds"] == 2.0
        assert snapshot["flux.loop_lag_p95_seconds"] == 0.01  # single spike ignored by p95

    def test_failure_and_crash_rates(self):
        collector = self._collector()
        for outcome in ("completed",) * 6 + ("failed",) * 2 + ("crashed",) * 2:
            collector.record_outcome(outcome)

        snapshot = collector.snapshot(running=0)

        assert snapshot["flux.failure_rate"] == 0.4  # failed + crashed
        assert snapshot["flux.crash_rate"] == 0.2
        assert snapshot["flux.executions_per_minute"] == 10.0

    def test_duration_p95_and_startup_median(self):
        collector = self._collector()
        for i in range(100):
            collector.record_duration(float(i))
        for value in (0.1, 0.2, 0.9):
            collector.record_startup(value)

        snapshot = collector.snapshot(running=0)

        assert snapshot["flux.execution_duration_p95_seconds"] == 94.0
        assert snapshot["flux.startup_overhead_seconds"] == 0.2  # median, spike-resistant

    def test_warm_modules_accessor(self):
        collector = self._collector(warm_modules=lambda: 7)
        assert collector.snapshot(running=0)["flux.warm_modules"] == 7.0


class TestBuiltinMergeInPong:
    @pytest.mark.asyncio
    async def test_builtins_published_without_provider(self):
        from flux.worker_metrics import WorkerMetricsCollector

        worker = make_worker()
        worker._metrics_collector = WorkerMetricsCollector()
        worker._metrics_interval = 60.0

        snapshot = await worker._collect_metrics()

        assert snapshot is not None
        assert snapshot["flux.running_executions"] == 0.0

    @pytest.mark.asyncio
    async def test_reserved_prefix_stripped_from_provider_output(self):
        from flux.worker_metrics import WorkerMetricsCollector

        worker = make_worker()
        worker._metrics_collector = WorkerMetricsCollector()
        worker._metrics_provider = lambda: {
            "fitness": 0.9,
            "flux.running_executions": 999.0,  # impersonation attempt
        }
        worker._metrics_interval = 60.0

        snapshot = await worker._collect_metrics()

        assert snapshot["fitness"] == 0.9
        assert snapshot["flux.running_executions"] == 0.0  # built-in wins

    @pytest.mark.asyncio
    async def test_provider_failure_keeps_user_values_but_refreshes_builtins(self):
        from flux.worker_metrics import WorkerMetricsCollector

        worker = make_worker()
        worker._metrics_collector = WorkerMetricsCollector()
        worker._metrics_provider = lambda: {"fitness": 0.7}
        worker._metrics_interval = 0.0001

        first = await worker._collect_metrics()
        assert first["fitness"] == 0.7

        def boom():
            raise RuntimeError("collector down")

        worker._metrics_provider = boom
        worker._metrics_collector.record_outcome("failed")
        await asyncio.sleep(0.001)
        second = await worker._collect_metrics()

        assert second["fitness"] == 0.7  # user snapshot survives the failure
        assert second["flux.failure_rate"] == 1.0  # built-ins still refreshed
