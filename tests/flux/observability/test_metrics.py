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
        m.record_workflow_started("default", "my_workflow")

        metric = self._get_metric(reader, "flux_workflow_executions_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["workflow_name"] == "my_workflow"
        assert dp.attributes["status"] == "started"
        assert dp.value == 1

    def test_record_workflow_completed(self):
        m, reader = self._create_metrics()
        m.record_workflow_completed("default", "my_workflow", "completed", 1.5)

        metric = self._get_metric(reader, "flux_workflow_executions_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["workflow_name"] == "my_workflow"
        assert dp.attributes["status"] == "completed"
        assert dp.value == 1

        duration = self._get_metric(reader, "flux_workflow_execution_duration_seconds")
        assert duration is not None
        assert duration.data.data_points[0].sum == 1.5

    def test_record_workflow_completed_zero_duration_skips_histogram(self):
        m, reader = self._create_metrics()
        m.record_workflow_completed("default", "my_workflow", "cancelled", 0)

        metric = self._get_metric(reader, "flux_workflow_executions_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["status"] == "cancelled"

        duration = self._get_metric(reader, "flux_workflow_execution_duration_seconds")
        assert duration is None

    def test_record_task_started(self):
        m, reader = self._create_metrics()
        m.record_task_started("default", "wf", "my_task")

        metric = self._get_metric(reader, "flux_task_executions_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["workflow_name"] == "wf"
        assert dp.attributes["task_name"] == "my_task"
        assert dp.attributes["status"] == "started"

    def test_record_task_completed(self):
        m, reader = self._create_metrics()
        m.record_task_completed("default", "wf", "my_task", "completed", 0.5)

        metric = self._get_metric(reader, "flux_task_executions_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["status"] == "completed"
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["workflow_name"] == "wf"
        assert dp.attributes["task_name"] == "my_task"

        duration = self._get_metric(reader, "flux_task_execution_duration_seconds")
        assert duration is not None
        assert duration.data.data_points[0].sum == 0.5

    def test_record_task_retry(self):
        m, reader = self._create_metrics()
        m.record_task_retry("default", "wf", "my_task")

        metric = self._get_metric(reader, "flux_task_retries_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["workflow_name"] == "wf"
        assert dp.attributes["task_name"] == "my_task"
        assert dp.value == 1

    def test_record_worker_registered(self):
        m, reader = self._create_metrics()
        m.record_worker_registered("worker-1")

        registrations = self._get_metric(reader, "flux_worker_registrations_total")
        assert registrations is not None
        dp = registrations.data.data_points[0]
        assert dp.attributes["worker_name"] == "worker-1"
        assert dp.value == 1

    def test_record_worker_connected(self):
        m, reader = self._create_metrics()
        m.record_worker_connected("worker-1")

        active = self._get_metric(reader, "flux_workers_active")
        assert active is not None

    def test_record_worker_disconnected(self):
        m, reader = self._create_metrics()
        m.record_worker_disconnected("worker-1", "evicted")

        metric = self._get_metric(reader, "flux_worker_disconnections_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["worker_name"] == "worker-1"
        assert dp.attributes["reason"] == "evicted"
        assert dp.value == 1

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
        dp = metric.data.data_points[0]
        assert dp.attributes["schedule_name"] == "nightly"
        assert dp.attributes["outcome"] == "success"

    def test_record_http_request(self):
        m, reader = self._create_metrics()
        m.record_http_request("GET", "/workflows", 200, 0.05)

        count = self._get_metric(reader, "flux_http_requests_total")
        assert count is not None
        dp = count.data.data_points[0]
        assert dp.attributes["method"] == "GET"
        assert dp.attributes["endpoint"] == "/workflows"
        assert dp.attributes["status_code"] == "200"

        duration = self._get_metric(reader, "flux_http_request_duration_seconds")
        assert duration is not None
        assert duration.data.data_points[0].sum == 0.05

    def test_record_checkpoint_with_duration(self):
        m, reader = self._create_metrics()
        m.record_checkpoint("default", "my_workflow", 0.15)

        metric = self._get_metric(reader, "flux_checkpoints_total")
        assert metric is not None
        dp = metric.data.data_points[0]
        assert dp.attributes["workflow_namespace"] == "default"
        assert dp.attributes["workflow_name"] == "my_workflow"

        duration = self._get_metric(reader, "flux_checkpoint_duration_seconds")
        assert duration is not None
        assert duration.data.data_points[0].sum == 0.15

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
        hit_dp = None
        miss_dp = None
        for dp in metric.data.data_points:
            if dp.attributes["result"] == "hit":
                hit_dp = dp
            elif dp.attributes["result"] == "miss":
                miss_dp = dp
        assert hit_dp is not None and hit_dp.value == 1
        assert miss_dp is not None and miss_dp.value == 1


def test_metrics_helpers_require_namespace_parameter():
    """Signature check: metrics helpers must accept namespace as first arg."""
    import inspect
    from flux.observability.metrics import FluxMetrics

    for helper in [
        "record_workflow_started",
        "record_workflow_completed",
        "record_task_started",
        "record_task_completed",
        "record_task_retry",
        "record_checkpoint",
    ]:
        sig = inspect.signature(getattr(FluxMetrics, helper))
        params = list(sig.parameters.keys())
        # params[0] is 'self'; the next positional argument should be 'namespace'
        assert "namespace" in params, f"{helper} missing 'namespace' parameter"
        assert params.index("namespace") < params.index(
            "workflow_name",
        ), f"{helper}: namespace should come before workflow_name"


class TestNormalizePath:
    def test_normalizes_worker_paths(self):
        assert (
            _normalize_path("/workers/worker-abc123/claim/exec-456")
            == "/workers/{worker}/claim/{execution_id}"
        )

    def test_normalizes_checkpoint_path(self):
        assert (
            _normalize_path("/workers/worker-abc123/checkpoint/exec-456")
            == "/workers/{worker}/checkpoint/{execution_id}"
        )

    def test_normalizes_workflow_name_in_paths(self):
        assert (
            _normalize_path("/workflows/hello_world/executions/abc123")
            == "/workflows/{workflow_name}/executions/{execution_id}"
        )
        assert (
            _normalize_path("/workflows/my_pipeline/run/async")
            == "/workflows/{workflow_name}/run/async"
        )
        assert (
            _normalize_path("/workflows/my_pipeline/cancel/exec-123")
            == "/workflows/{workflow_name}/cancel/exec-123"
        )

    def test_preserves_simple_paths(self):
        assert _normalize_path("/workflows") == "/workflows"
        assert _normalize_path("/health") == "/health"
        assert _normalize_path("/metrics") == "/metrics"

    def test_normalizes_4_segment_namespaced_run_route(self):
        normalized = _normalize_path("/workflows/billing/invoice/run/sync")
        assert "billing" not in normalized
        assert "invoice" not in normalized

    def test_normalizes_4_segment_namespaced_resume_route(self):
        normalized = _normalize_path("/workflows/billing/invoice/resume/exec-123/sync")
        assert "billing" not in normalized
        assert "invoice" not in normalized
        assert "exec-123" not in normalized

    def test_normalizes_4_segment_namespaced_executions_route(self):
        normalized = _normalize_path("/workflows/billing/invoice/executions")
        assert "billing" not in normalized
        assert "invoice" not in normalized

    def test_normalizes_4_segment_namespaced_workflow_resource(self):
        normalized = _normalize_path("/workflows/billing/invoice")
        assert "billing" not in normalized
        assert "invoice" not in normalized

    def test_normalizes_legacy_3_segment_run_route(self):
        normalized = _normalize_path("/workflows/hello_world/run/sync")
        assert "hello_world" not in normalized

    def test_normalizes_legacy_3_segment_executions_route(self):
        normalized = _normalize_path("/workflows/hello_world/executions/abc123")
        assert "hello_world" not in normalized

    def test_4_segment_namespaced_path_not_double_rewritten(self):
        normalized = _normalize_path("/workflows/billing/invoice/run/sync")
        assert "{workflow_name}" not in normalized
        assert "{namespace}" in normalized or "billing" not in normalized
