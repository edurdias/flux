"""Tests for observability metric instruments."""

from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from flux.observability.metrics import FluxMetrics


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

    def test_record_workflow_completed(self):
        m, reader = self._create_metrics()
        m.record_workflow_completed("my_workflow", "completed", 1.5)

        metric = self._get_metric(reader, "flux_workflow_executions_total")
        assert metric is not None

        duration = self._get_metric(reader, "flux_workflow_duration_seconds")
        assert duration is not None

    def test_record_workflow_started(self):
        m, reader = self._create_metrics()
        m.record_execution_started("my_workflow")

        metric = self._get_metric(reader, "flux_active_executions")
        assert metric is not None

    def test_record_task_completed(self):
        m, reader = self._create_metrics()
        m.record_task_completed("wf", "my_task", "completed", 0.5)

        metric = self._get_metric(reader, "flux_task_executions_total")
        assert metric is not None

        duration = self._get_metric(reader, "flux_task_duration_seconds")
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

    def test_record_checkpoint(self):
        m, reader = self._create_metrics()
        m.record_checkpoint("my_workflow")

        metric = self._get_metric(reader, "flux_checkpoint_total")
        assert metric is not None

    def test_queue_depth(self):
        m, reader = self._create_metrics()
        m.record_execution_queued()
        m.record_execution_claimed()

        metric = self._get_metric(reader, "flux_execution_queue_depth")
        assert metric is not None
