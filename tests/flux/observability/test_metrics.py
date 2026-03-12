"""Tests for observability metric instruments."""

from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from flux.observability.metrics import FluxMetrics, _normalize_path


class TestFluxMetrics:
    def _create_metrics(self) -> tuple[FluxMetrics, InMemoryMetricReader]:
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        m = FluxMetrics(provider.get_meter("flux-test"))
        return m, reader

    def _get_metric(self, reader, name):
        data = reader.get_metrics_data()
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        return metric
        return None

    def test_record_workflow_started(self):
        m, reader = self._create_metrics()
        m.record_workflow_started("my_workflow")

        metric = self._get_metric(reader, "flux_workflow_executions_total")
        assert metric is not None

    def test_record_workflow_completed(self):
        m, reader = self._create_metrics()
        m.record_workflow_completed("my_workflow", "completed", 1.5)

        metric = self._get_metric(reader, "flux_workflow_executions_total")
        assert metric is not None

        duration = self._get_metric(reader, "flux_workflow_execution_duration_seconds")
        assert duration is not None

    def test_record_task_started(self):
        m, reader = self._create_metrics()
        m.record_task_started("wf", "my_task")

        metric = self._get_metric(reader, "flux_task_executions_total")
        assert metric is not None

    def test_record_task_completed(self):
        m, reader = self._create_metrics()
        m.record_task_completed("wf", "my_task", "completed", 0.5)

        metric = self._get_metric(reader, "flux_task_executions_total")
        assert metric is not None

        duration = self._get_metric(reader, "flux_task_execution_duration_seconds")
        assert duration is not None

    def test_record_task_retry(self):
        m, reader = self._create_metrics()
        m.record_task_retry("wf", "my_task")

        metric = self._get_metric(reader, "flux_task_retries_total")
        assert metric is not None

    def test_record_worker_connected(self):
        m, reader = self._create_metrics()
        m.record_worker_connected("worker-1")

        registrations = self._get_metric(reader, "flux_worker_registrations_total")
        assert registrations is not None

    def test_record_worker_disconnected(self):
        m, reader = self._create_metrics()
        m.record_worker_disconnected("worker-1", "evicted")

        metric = self._get_metric(reader, "flux_worker_disconnections_total")
        assert metric is not None

    def test_record_worker_execution_lifecycle(self):
        m, reader = self._create_metrics()
        m.record_worker_execution_started("worker-1")
        m.record_worker_execution_ended("worker-1")

        metric = self._get_metric(reader, "flux_worker_executions_active")
        assert metric is not None

    def test_record_schedule_trigger(self):
        m, reader = self._create_metrics()
        m.record_schedule_trigger("nightly", "success")

        metric = self._get_metric(reader, "flux_schedule_triggers_total")
        assert metric is not None

    def test_record_http_request(self):
        m, reader = self._create_metrics()
        m.record_http_request("GET", "/workflows", 200, 0.05)

        count = self._get_metric(reader, "flux_http_requests_total")
        assert count is not None

        duration = self._get_metric(reader, "flux_http_request_duration_seconds")
        assert duration is not None

    def test_record_checkpoint_with_duration(self):
        m, reader = self._create_metrics()
        m.record_checkpoint("my_workflow", 0.15)

        metric = self._get_metric(reader, "flux_checkpoints_total")
        assert metric is not None

        duration = self._get_metric(reader, "flux_checkpoint_duration_seconds")
        assert duration is not None

    def test_queue_depth_and_schedule_to_start(self):
        m, reader = self._create_metrics()
        m.record_execution_queued()
        m.record_execution_claimed(schedule_to_start=0.08)

        metric = self._get_metric(reader, "flux_execution_queue_depth")
        assert metric is not None

        latency = self._get_metric(reader, "flux_execution_schedule_to_start_seconds")
        assert latency is not None

    def test_record_module_cache(self):
        m, reader = self._create_metrics()
        m.record_module_cache("hit")
        m.record_module_cache("miss")

        metric = self._get_metric(reader, "flux_module_cache_total")
        assert metric is not None


class TestNormalizePath:
    def test_normalizes_worker_paths(self):
        assert _normalize_path("/workers/worker-abc123/claim/exec-456") == \
            "/workers/{worker}/claim/{execution_id}"

    def test_normalizes_checkpoint_path(self):
        assert _normalize_path("/workers/worker-abc123/checkpoint/exec-456") == \
            "/workers/{worker}/checkpoint/{execution_id}"

    def test_normalizes_execution_path(self):
        assert _normalize_path("/workflows/hello_world/executions/abc123") == \
            "/workflows/hello_world/executions/{execution_id}"

    def test_preserves_simple_paths(self):
        assert _normalize_path("/workflows") == "/workflows"
        assert _normalize_path("/health") == "/health"
        assert _normalize_path("/metrics") == "/metrics"
