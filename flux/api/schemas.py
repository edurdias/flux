"""Shared request/response schemas, constants, and helpers for the HTTP API.

Extracted from ``flux/server.py`` so the per-domain route modules in
``flux.api`` can import them without creating an import cycle with the
``Server`` class.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded


MAX_WORKFLOW_UPLOAD_BYTES = 1_048_576  # 1 MiB — workflow sources should be small
SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


class WorkerRuntimeModel(BaseModel):
    os_name: str
    os_version: str
    python_version: str


class WorkerGPUModel(BaseModel):
    name: str
    memory_total: float
    memory_available: float


class WorkerResourcesModel(BaseModel):
    cpu_total: float
    cpu_available: float
    memory_total: float
    memory_available: float
    disk_total: float
    disk_free: float
    gpus: list[WorkerGPUModel]


class WorkerRegistration(BaseModel):
    name: str
    runtime: WorkerRuntimeModel
    packages: list[dict[str, str]]
    resources: WorkerResourcesModel
    labels: dict[str, str] = Field(default_factory=dict)
    # Advertised capacity; None/0 means unlimited (legacy workers).
    max_concurrent_executions: int | None = None
    # Runners this worker has enabled; None means a legacy worker that
    # predates runners and executes everything in-process.
    runners: list[str] | None = None


class SecretRequest(BaseModel):
    """Model for secret creation/update requests"""

    name: str
    value: Any


class SecretResponse(BaseModel):
    """Model for secret responses"""

    name: str
    value: Any | None = None


class ConfigRequest(BaseModel):
    name: str
    value: Any


class ScheduleRequest(BaseModel):
    """Model for schedule creation/update requests"""

    workflow_name: str
    workflow_namespace: str | None = None
    name: str
    schedule_config: dict  # Schedule configuration (cron expression, interval, etc.)
    description: str | None = None
    input_data: Any | None = None
    run_as_service_account: str | None = None


class ScheduleResponse(BaseModel):
    """Model for schedule responses"""

    id: str
    workflow_id: str
    workflow_namespace: str
    workflow_name: str
    name: str
    description: str | None
    schedule_type: str
    status: str
    created_at: str
    updated_at: str
    last_run_at: str | None
    next_run_at: str | None
    run_count: int
    failure_count: int
    run_as_service_account: str | None = None


class ScheduleUpdateRequest(BaseModel):
    """Model for schedule update requests"""

    schedule_config: dict | None = None
    description: str | None = None
    input_data: Any | None = None
    run_as_service_account: str | None = None


class RoleRequest(BaseModel):
    name: str
    permissions: list[str]


class RoleUpdateRequest(BaseModel):
    add_permissions: list[str] | None = None
    remove_permissions: list[str] | None = None


class RoleCloneRequest(BaseModel):
    new_name: str


class ApprovalDecideRequest(BaseModel):
    """Body for POST /executions/{id}/approvals/{call}/{approve|reject}.

    The decision verb (approve/reject) is path-derived; only the optional reason
    travels in the body.
    """

    reason: str | None = None


class APIKeyRequest(BaseModel):
    name: str
    expires_in_days: int | None = None


class TestTokenRequest(BaseModel):
    token: str


class PrincipalCreateRequest(BaseModel):
    subject: str
    type: str
    external_issuer: str | None = None
    display_name: str | None = None
    roles: list[str] = Field(default_factory=list)


class PrincipalUpdateRequest(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None


class RoleGrantRequest(BaseModel):
    role: str


class PrincipalResponse(BaseModel):
    id: str
    subject: str
    type: str
    external_issuer: str
    display_name: str | None
    enabled: bool
    roles: list[str]


# New response models for missing endpoints
class WorkflowVersionResponse(BaseModel):
    """Model for workflow version responses"""

    id: str
    name: str
    version: int


class ExecutionSummaryResponse(BaseModel):
    """Model for execution summary responses"""

    execution_id: str
    workflow_id: str
    workflow_namespace: str
    workflow_name: str
    state: str
    worker_name: str | None = None


class ExecutionListResponse(BaseModel):
    """Model for execution list responses"""

    executions: list[ExecutionSummaryResponse]
    total: int
    limit: int
    offset: int


class WorkerResponse(BaseModel):
    """Model for worker responses"""

    name: str
    status: str = "offline"
    runtime: WorkerRuntimeModel | None = None
    resources: WorkerResourcesModel | None = None
    packages: list[dict[str, str]] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    # Latest advertised metrics snapshot (routing "metric:*" selectors).
    metrics: dict[str, float] | None = None


class HealthResponse(BaseModel):
    """Model for health check responses"""

    status: str
    database: bool
    version: str


class AgentSessionSummaryResponse(BaseModel):
    """Model for an agent-session row."""

    execution_id: str
    agent_name: str
    state: str
    started_at: str | None = None
    workflow_namespace: str
    workflow_name: str
    current_worker: str | None = None


class AgentSessionListResponse(BaseModel):
    """Model for agent-session list responses."""

    sessions: list[AgentSessionSummaryResponse]
    total: int
    limit: int
    offset: int


class ScheduleHistoryEntry(BaseModel):
    """Model for schedule history entry"""

    execution_id: str
    workflow_name: str
    state: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class ScheduleHistoryResponse(BaseModel):
    """Model for schedule history responses"""

    schedule_id: str
    workflow_name: str
    entries: list[ScheduleHistoryEntry]
    total: int
    limit: int
    offset: int


def _has_any_workflow_read(permissions: set[str]) -> bool:
    if "*" in permissions:
        return True
    for p in permissions:
        parts = p.split(":")
        if parts[0] == "workflow" and (parts[-1] == "read" or parts[-1] == "*"):
            return True
    return False


def _inject_trace_context(data_payload: str) -> str:
    """Add the current OTel trace context to an SSE JSON payload, if enabled.

    Workers use this to continue the server-side trace; without it resumed and
    cancelled executions would start a disconnected trace.
    """
    from flux.observability import is_enabled

    if not is_enabled():
        return data_payload

    import json as _json

    from flux.observability.tracing import inject_trace_context

    try:
        event_data = _json.loads(data_payload)
        event_data["trace_context"] = inject_trace_context()
        return _json.dumps(event_data)
    except Exception:
        return data_payload
