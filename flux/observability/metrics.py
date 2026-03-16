"""Flux metric instruments and recording helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.metrics import Meter

# Patterns for normalizing high-cardinality path segments
_PATH_PATTERNS = [
    (re.compile(r"/workers/[^/]+/"), "/workers/{worker}/"),
    (re.compile(r"/claim/[^/]+"), "/claim/{execution_id}"),
    (re.compile(r"/checkpoint/[^/]+"), "/checkpoint/{execution_id}"),
    (re.compile(r"/workflows/[^/]+/"), "/workflows/{workflow_name}/"),
    (re.compile(r"/executions/[^/]+"), "/executions/{execution_id}"),
    (re.compile(r"/schedules/[^/]+"), "/schedules/{schedule_id}"),
]


def _normalize_path(path: str) -> str:
    for pattern, replacement in _PATH_PATTERNS:
        path = pattern.sub(replacement, path)
    return path


class FluxMetrics:
    """Flux metric instruments."""

    def __init__(self, meter: Meter):
        self.workflow_executions = meter.create_counter(
            "flux_workflow_executions_total",
            description="Workflow executions by status",
        )
        self.workflow_execution_duration = meter.create_histogram(
            "flux_workflow_execution_duration_seconds",
            description="Workflow execution duration in seconds",
            unit="s",
        )

        self.task_executions = meter.create_counter(
            "flux_task_executions_total",
            description="Task executions by status",
        )
        self.task_execution_duration = meter.create_histogram(
            "flux_task_execution_duration_seconds",
            description="Task execution duration in seconds",
            unit="s",
        )
        self.task_retries = meter.create_counter(
            "flux_task_retries_total",
            description="Task retry attempts",
        )

        self.execution_queue_depth = meter.create_up_down_counter(
            "flux_execution_queue_depth",
            description="Executions waiting for workers",
        )
        self.execution_schedule_to_start = meter.create_histogram(
            "flux_execution_schedule_to_start_seconds",
            description="Time from queued to worker claim",
            unit="s",
        )
        self.checkpoints = meter.create_counter(
            "flux_checkpoints_total",
            description="Checkpoint events",
        )
        self.checkpoint_duration = meter.create_histogram(
            "flux_checkpoint_duration_seconds",
            description="Checkpoint HTTP round-trip duration",
            unit="s",
        )

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
        self.worker_executions_active = meter.create_up_down_counter(
            "flux_worker_executions_active",
            description="Concurrent executions per worker",
        )

        self.schedule_triggers = meter.create_counter(
            "flux_schedule_triggers_total",
            description="Schedule trigger events",
        )

        self.http_requests = meter.create_counter(
            "flux_http_requests_total",
            description="HTTP request count",
        )
        self.http_request_duration = meter.create_histogram(
            "flux_http_request_duration_seconds",
            description="HTTP request duration in seconds",
            unit="s",
        )

        self.module_cache = meter.create_counter(
            "flux_module_cache_total",
            description="Module cache lookups by result",
        )

    def record_workflow_started(self, workflow_name: str):
        self.workflow_executions.add(1, {"workflow_name": workflow_name, "status": "started"})

    def record_workflow_completed(self, workflow_name: str, status: str, duration: float):
        self.workflow_executions.add(1, {"workflow_name": workflow_name, "status": status})
        if duration > 0:
            self.workflow_execution_duration.record(duration, {"workflow_name": workflow_name})

    def record_task_started(self, workflow_name: str, task_name: str):
        self.task_executions.add(
            1,
            {"workflow_name": workflow_name, "task_name": task_name, "status": "started"},
        )

    def record_task_completed(
        self,
        workflow_name: str,
        task_name: str,
        status: str,
        duration: float,
    ):
        attrs = {"workflow_name": workflow_name, "task_name": task_name, "status": status}
        self.task_executions.add(1, attrs)
        self.task_execution_duration.record(
            duration,
            {"workflow_name": workflow_name, "task_name": task_name},
        )

    def record_task_retry(self, workflow_name: str, task_name: str):
        self.task_retries.add(1, {"workflow_name": workflow_name, "task_name": task_name})

    def record_execution_queued(self):
        self.execution_queue_depth.add(1)

    def record_execution_claimed(self, schedule_to_start: float | None = None):
        self.execution_queue_depth.add(-1)
        if schedule_to_start is not None:
            self.execution_schedule_to_start.record(schedule_to_start)

    def record_checkpoint(self, workflow_name: str, duration: float | None = None):
        self.checkpoints.add(1, {"workflow_name": workflow_name})
        if duration is not None:
            self.checkpoint_duration.record(duration, {"workflow_name": workflow_name})

    def record_worker_registered(self, worker_name: str):
        self.worker_registrations.add(1, {"worker_name": worker_name})

    def record_worker_connected(self, worker_name: str):
        self.workers_active.add(1)

    def record_worker_disconnected(self, worker_name: str, reason: str):
        self.worker_disconnections.add(1, {"worker_name": worker_name, "reason": reason})
        self.workers_active.add(-1)

    def record_worker_execution_started(self, worker_name: str):
        self.worker_executions_active.add(1, {"worker_name": worker_name})

    def record_worker_execution_ended(self, worker_name: str):
        self.worker_executions_active.add(-1, {"worker_name": worker_name})

    def record_schedule_trigger(self, schedule_name: str, outcome: str):
        self.schedule_triggers.add(1, {"schedule_name": schedule_name, "outcome": outcome})

    def record_http_request(self, method: str, endpoint: str, status_code: int, duration: float):
        endpoint = _normalize_path(endpoint)
        attrs = {"method": method, "endpoint": endpoint, "status_code": str(status_code)}
        self.http_requests.add(1, attrs)
        self.http_request_duration.record(duration, {"method": method, "endpoint": endpoint})

    def record_module_cache(self, result: str):
        self.module_cache.add(1, {"result": result})
