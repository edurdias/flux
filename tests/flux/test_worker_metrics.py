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


def _lifecycle_ctx(execution_id="exec-lc", transient=False, finished=False, failed=False):
    ctx = MagicMock()
    ctx.execution_id = execution_id
    ctx.workflow_name = "wf"
    ctx.workflow_namespace = "default"
    ctx.is_transient = transient
    ctx.is_paused = False
    ctx.has_finished = finished
    ctx.has_failed = failed
    ctx.state.value = "COMPLETED" if finished else "RUNNING"
    ctx.events = []
    ctx.to_dict.return_value = {"execution_id": execution_id}
    return ctx


class TestLifecycleRecording:
    """The worker feeds the collector from its real execution paths."""

    def _worker_with_collector(self):
        from flux.worker_metrics import WorkerMetricsCollector

        worker = make_worker()
        worker._metrics_collector = WorkerMetricsCollector()
        worker.client.post = MagicMock()

        async def ok_post(url, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.raise_for_status = MagicMock()
            return response

        worker.client.post = ok_post
        return worker

    @pytest.mark.asyncio
    async def test_first_intermediate_checkpoint_records_startup(self):
        import time as time_mod

        worker = self._worker_with_collector()
        ctx = _lifecycle_ctx()
        worker._execution_started[ctx.execution_id] = time_mod.monotonic() - 0.5

        await worker._checkpoint(ctx)

        assert worker._metrics_collector._startups, "startup sample not recorded"
        assert worker._metrics_collector._startups[0] >= 0.5
        assert ctx.execution_id not in worker._execution_started

    @pytest.mark.asyncio
    async def test_terminal_first_checkpoint_is_not_a_startup_signal(self):
        import time as time_mod

        worker = self._worker_with_collector()
        ctx = _lifecycle_ctx(finished=True)
        worker._execution_started[ctx.execution_id] = time_mod.monotonic()

        await worker._checkpoint(ctx)

        assert not worker._metrics_collector._startups

    @pytest.mark.asyncio
    async def test_transient_checkpoint_is_not_a_startup_signal(self):
        import time as time_mod

        worker = self._worker_with_collector()
        ctx = _lifecycle_ctx(transient=True, finished=True)
        worker._execution_started[ctx.execution_id] = time_mod.monotonic()

        await worker._checkpoint(ctx)

        assert not worker._metrics_collector._startups

    @pytest.mark.asyncio
    async def test_run_workflow_records_duration_and_outcome(self):
        worker = self._worker_with_collector()
        done = _lifecycle_ctx(finished=True)

        class FakeRunner:
            async def execute(self, request, hooks):
                return done

        worker._runners = {"subprocess": FakeRunner()}
        request = MagicMock()
        request.runner = None
        request.context = _lifecycle_ctx()
        request.workflow.name = "wf"
        request.workflow.namespace = "default"
        request.workflow.version = 1

        result = await worker._run_workflow(request, hooks=MagicMock())

        assert result is done
        assert list(worker._metrics_collector._outcomes) == ["completed"]
        assert len(worker._metrics_collector._durations) == 1
        assert request.context.execution_id not in worker._execution_started

    @pytest.mark.asyncio
    async def test_run_workflow_records_failed_outcome(self):
        worker = self._worker_with_collector()
        failed = _lifecycle_ctx(finished=True, failed=True)

        class FakeRunner:
            async def execute(self, request, hooks):
                return failed

        worker._runners = {"subprocess": FakeRunner()}
        request = MagicMock()
        request.runner = None
        request.context = _lifecycle_ctx()
        request.workflow.name = "wf"
        request.workflow.namespace = "default"
        request.workflow.version = 1

        await worker._run_workflow(request, hooks=MagicMock())

        assert list(worker._metrics_collector._outcomes) == ["failed"]

    @pytest.mark.asyncio
    async def test_runner_crash_records_crashed_outcome(self):
        from unittest.mock import AsyncMock

        from flux.errors import WorkerProcessCrashed

        worker = self._worker_with_collector()
        worker._checkpoint = AsyncMock()
        worker._release_claim = AsyncMock()
        request = MagicMock()
        request.context = _lifecycle_ctx(transient=True)
        crash = WorkerProcessCrashed("exec-lc", 137, last_context=None)

        await worker._handle_runner_crash(request, crash)

        assert list(worker._metrics_collector._outcomes) == ["crashed"]


class TestPerMinuteWindow:
    def test_old_completions_age_out_of_the_rate(self):
        from unittest.mock import patch

        from flux.worker_metrics import WorkerMetricsCollector

        collector = WorkerMetricsCollector()
        with patch("flux.worker_metrics.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            collector.record_outcome("completed")
            collector.record_outcome("completed")
            mock_time.monotonic.return_value = 190.0  # 90s later: out of window
            collector.record_outcome("completed")
            snapshot = collector.snapshot(running=0)

        assert snapshot["flux.executions_per_minute"] == 1.0


class TestLoaderWarmModules:
    def test_loader_size_counts_only_unexpired(self):
        import base64

        from flux.runners.loader import WorkflowModuleLoader

        loader = WorkflowModuleLoader(ttl=300, max_size=8)
        source = base64.b64encode(
            b"from flux import workflow\n\n\n@workflow\nasync def wf(ctx):\n    return 1\n",
        ).decode()
        loader.load("default", "wf", 1, source)

        assert loader.size() == 1
        # Expire it: size must drop without waiting for eviction-on-load.
        key, (module, _) = next(iter(loader._cache.items()))
        loader._cache[key] = (module, -10_000.0)
        assert loader.size() == 0

    def test_inprocess_runner_exposes_warm_modules(self):
        from flux.runners.inprocess import InProcessRunner

        runner = InProcessRunner()
        assert runner.warm_modules() == 0
