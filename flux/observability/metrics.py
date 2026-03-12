"""Flux metric instruments and recording helpers."""

from __future__ import annotations

from opentelemetry.metrics import Meter


class FluxMetrics:
    """All 14 Flux metric instruments."""

    def __init__(self, meter: Meter):
        # Workflow metrics
        self.workflow_executions = meter.create_counter(
            "flux_workflow_executions_total",
            description="Total workflow executions by status",
        )
        self.workflow_duration = meter.create_histogram(
            "flux_workflow_duration_seconds",
            description="Workflow execution duration in seconds",
            unit="s",
        )
        self.active_executions = meter.create_up_down_counter(
            "flux_active_executions",
            description="Currently running executions",
        )

        # Task metrics
        self.task_executions = meter.create_counter(
            "flux_task_executions_total",
            description="Total task executions by status",
        )
        self.task_duration = meter.create_histogram(
            "flux_task_duration_seconds",
            description="Task execution duration in seconds",
            unit="s",
        )
        self.task_retries = meter.create_counter(
            "flux_task_retries_total",
            description="Total task retry attempts",
        )

        # Worker metrics
        self.workers_active = meter.create_up_down_counter(
            "flux_workers_active",
            description="Currently connected workers",
        )
        self.worker_registrations = meter.create_counter(
            "flux_worker_registrations_total",
            description="Worker registration events",
        )
        self.worker_disconnections = meter.create_counter(
            "flux_worker_disconnections_total",
            description="Worker disconnection events",
        )

        # Schedule metrics
        self.schedule_triggers = meter.create_counter(
            "flux_schedule_triggers_total",
            description="Schedule trigger events",
        )

        # HTTP metrics
        self.http_requests = meter.create_counter(
            "flux_http_requests_total",
            description="HTTP request count",
        )
        self.http_duration = meter.create_histogram(
            "flux_http_request_duration_seconds",
            description="HTTP request duration in seconds",
            unit="s",
        )

        # Pipeline metrics
        self.checkpoints = meter.create_counter(
            "flux_checkpoint_total",
            description="Checkpoint events",
        )
        self.queue_depth = meter.create_up_down_counter(
            "flux_execution_queue_depth",
            description="Pending executions waiting for workers",
        )

    # --- Recording helpers ---

    def record_workflow_completed(self, workflow_name: str, status: str, duration: float):
        self.workflow_executions.add(1, {"workflow_name": workflow_name, "status": status})
        self.workflow_duration.record(duration, {"workflow_name": workflow_name})

    def record_execution_started(self, workflow_name: str):
        self.active_executions.add(1, {"workflow_name": workflow_name})

    def record_execution_ended(self, workflow_name: str):
        self.active_executions.add(-1, {"workflow_name": workflow_name})

    def record_task_completed(
        self, workflow_name: str, task_name: str, status: str, duration: float
    ):
        attrs = {"workflow_name": workflow_name, "task_name": task_name, "status": status}
        self.task_executions.add(1, attrs)
        self.task_duration.record(
            duration, {"workflow_name": workflow_name, "task_name": task_name}
        )

    def record_task_retry(self, workflow_name: str, task_name: str):
        self.task_retries.add(1, {"workflow_name": workflow_name, "task_name": task_name})

    def record_worker_connected(self, worker_name: str):
        self.worker_registrations.add(1, {"worker_name": worker_name})
        self.workers_active.add(1)

    def record_worker_disconnected(self, worker_name: str, reason: str):
        self.worker_disconnections.add(1, {"worker_name": worker_name, "reason": reason})
        self.workers_active.add(-1)

    def record_schedule_trigger(self, schedule_name: str, outcome: str):
        self.schedule_triggers.add(1, {"schedule_name": schedule_name, "outcome": outcome})

    def record_http_request(self, method: str, endpoint: str, status_code: int, duration: float):
        attrs = {"method": method, "endpoint": endpoint, "status_code": str(status_code)}
        self.http_requests.add(1, attrs)
        self.http_duration.record(duration, {"method": method, "endpoint": endpoint})

    def record_checkpoint(self, workflow_name: str):
        self.checkpoints.add(1, {"workflow_name": workflow_name})

    def record_execution_queued(self):
        self.queue_depth.add(1)

    def record_execution_claimed(self):
        self.queue_depth.add(-1)
