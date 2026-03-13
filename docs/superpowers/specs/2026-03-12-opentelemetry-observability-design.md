# OpenTelemetry Observability Design

**Date:** 2026-03-12
**Flux Version:** 0.9.0
**Status:** Approved

---

## Goal

Add full observability to Flux — metrics, distributed tracing, and log correlation — using OpenTelemetry. Expose a Prometheus `/metrics` endpoint and support OTLP push export. The feature is opt-in, zero overhead when disabled.

## Architecture

A new `flux/observability/` package owns all OTel setup and provides lightweight helper functions that the rest of the codebase calls. When observability is disabled, all helpers are no-ops.

### Module Structure

```
flux/observability/
├── __init__.py          # Public API: setup(), get_meter(), get_tracer(), shutdown()
├── config.py            # ObservabilityConfig (Pydantic model)
├── provider.py          # Initializes MeterProvider, TracerProvider, LoggerProvider
├── metrics.py           # All 14 metric instruments + helper functions to record them
├── middleware.py        # FastAPI middleware for HTTP metrics + trace context extraction
├── tracing.py           # Span decorators/context managers + W3C propagation helpers
└── logging.py           # OTel log handler that attaches to existing logger hierarchy
```

### Lifecycle

1. `setup(config)` called during `Server.__init__` when `observability.enabled = true`
2. Initializes three OTel providers (metrics, traces, logs) with configured exporters
3. Registers Prometheus exporter on `/metrics` route
4. Adds HTTP middleware to FastAPI app
5. Adds OTel log handler to the root `"flux"` logger
6. `shutdown()` called during FastAPI lifespan teardown — flushes all pending exports

### When Disabled

`setup()` is never called. Helper functions check a module-level flag and return immediately. No OTel imports in business logic files. Zero overhead.

---

## Configuration

New nested config under `FluxConfig`:

```python
class ObservabilityConfig(BaseModel):
    enabled: bool = False
    service_name: str = "flux"

    # Exporters
    otlp_endpoint: str | None = None        # e.g. "http://localhost:4317"
    otlp_protocol: str = "grpc"             # "grpc" or "http/protobuf"
    prometheus_enabled: bool = True          # /metrics endpoint

    # Tracing
    trace_sample_rate: float = 1.0          # 0.0 to 1.0

    # Export intervals
    metric_export_interval: int = 60        # seconds (for OTLP push)

    # Resource attributes (optional extras)
    resource_attributes: dict[str, str] = {}
```

Env vars follow existing pattern: `FLUX_OBSERVABILITY_ENABLED=true`, `FLUX_OBSERVABILITY_OTLP_ENDPOINT=http://collector:4317`, etc.

**Behavior:**
- `enabled: false` (default) — nothing initializes, no-op everywhere
- `enabled: true, otlp_endpoint: null` — Prometheus `/metrics` only, no push
- `enabled: true, otlp_endpoint: set` — both Prometheus and OTLP push

---

## Metrics (14 Instruments)

### Workflow Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_workflow_executions_total` | Counter | `workflow_name`, `status` | Completed/failed/cancelled executions |
| `flux_workflow_duration_seconds` | Histogram | `workflow_name` | End-to-end workflow execution time |
| `flux_active_executions` | UpDownCounter | `workflow_name` | Currently running executions |

### Task Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_task_executions_total` | Counter | `workflow_name`, `task_name`, `status` | Completed/failed tasks |
| `flux_task_duration_seconds` | Histogram | `workflow_name`, `task_name` | Per-task execution time |
| `flux_task_retries_total` | Counter | `workflow_name`, `task_name` | Retry attempts |

### Worker Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_workers_active` | UpDownCounter | — | Connected workers |
| `flux_worker_registrations_total` | Counter | `worker_name` | Registration events |
| `flux_worker_disconnections_total` | Counter | `worker_name`, `reason` | Disconnection/eviction events |

### Schedule Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_schedule_triggers_total` | Counter | `schedule_name`, `outcome` | Schedule trigger outcomes |

### HTTP Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_http_requests_total` | Counter | `method`, `endpoint`, `status_code` | HTTP request count |
| `flux_http_request_duration_seconds` | Histogram | `method`, `endpoint` | HTTP request latency |

### Execution Pipeline Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_checkpoint_total` | Counter | `workflow_name` | Worker checkpoint events |
| `flux_execution_queue_depth` | UpDownCounter | — | Pending executions waiting for workers |

### Recording Points

- Workflow/task metrics: `server.py` (lifecycle) and `worker.py` (execution)
- Worker metrics: `server.py` (register, disconnect, eviction)
- HTTP metrics: FastAPI middleware
- Schedule metrics: `schedule_manager.py`
- Queue depth: incremented on `_create_execution()`, decremented on worker claim

---

## Distributed Tracing

### Span Hierarchy

```
[Server] http POST /workflows/{name}/run/async
  └─ [Server] flux.execution.create {workflow_name, execution_id}
  └─ [Server] flux.execution.dispatch {worker_name}
      ── (trace context propagated via SSE event payload) ──
      └─ [Worker] flux.execution.claim {execution_id}
      └─ [Worker] flux.workflow.execute {workflow_name}
          └─ [Worker] flux.task.execute {task_name}
              └─ [Worker] flux.task.retry {attempt}  (if retries)
          └─ [Worker] flux.task.execute {task_name}
      └─ [Worker] flux.execution.checkpoint
```

### Context Propagation

- **Server → Worker:** inject `traceparent`/`tracestate` (W3C format) into SSE event JSON data as a `trace_context` dict
- **Worker → Server:** manual W3C trace context injection into HTTP headers on checkpoint/claim POSTs (using `opentelemetry.propagate`)

### Span Attributes

Custom attributes follow `flux.*` namespace:
- `flux.workflow.name`, `flux.execution.id`, `flux.task.name`, `flux.worker.name`
- Standard HTTP attributes added by middleware: `http.method`, `http.route`, `http.status_code`

### Error Recording

- Failed tasks/workflows set span status to ERROR with exception info
- Retries recorded as span events within the task span

### Instrumentation API

- `@traced("span.name")` decorator for functions
- `start_span("name")` async context manager for blocks
- `inject_trace_context() -> dict` and `extract_trace_context(data: dict) -> Context` for propagation
- All become no-ops when disabled

---

## Logging Integration

- On `setup()`, an `OTelLogHandler` is added to the root `"flux"` logger
- The handler injects `trace_id` and `span_id` into log records for correlation when emitted inside an active span
- Existing console output is completely unchanged — the handler is additive
- When observability is enabled, the console log format is enriched with `%(otelTraceID)s %(otelSpanID)s` for manual correlation
- When disabled: no handler added, no format changes, zero impact

---

## Dependencies

New optional dependencies in an `observability` extras group:

```toml
[tool.poetry.extras]
observability = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp",
    "opentelemetry-exporter-prometheus",
    "opentelemetry-instrumentation-fastapi",
    "opentelemetry-instrumentation-httpx",
    "opentelemetry-instrumentation-logging",
]
```

Install with `pip install flux-core[observability]`. The `enabled: true` config check verifies packages are importable and gives a clear error if not.

---

## Integration Points (Existing File Changes)

### `flux/config.py`
- Add `ObservabilityConfig` model
- Add `observability: ObservabilityConfig` field to `FluxConfig`

### `flux/server.py` (5 touch points)
1. `__init__`: call `observability.setup()` if enabled
2. `_create_api`: add middleware, add `/metrics` route
3. `_create_execution`: increment queue depth, start execution span
4. Worker register/disconnect/eviction: record worker metrics
5. Lifespan shutdown: call `observability.shutdown()`

### `flux/worker.py` (3 touch points)
1. `_handle_execution_scheduled`: extract trace context from SSE event, start workflow span
2. Task execution: start task spans, record task metrics (duration, retries)
3. `_send_checkpoint`: record checkpoint counter

### `flux/schedule_manager.py` (1 touch point)
1. `_trigger_scheduled_workflow`: record schedule trigger counter

### `flux/task.py` (2 touch points)
1. Task execution: wrap in span, record duration histogram
2. Retry/fallback: record retry counter, add span events

**Key principle:** Each touch point is a single function call to the observability module. No OTel imports in these files.

---

## Testing Strategy

Unit tests in `tests/flux/observability/`:

- `test_config.py` — defaults, custom values, env var overrides
- `test_metrics.py` — each metric instrument records correctly, no-op when disabled
- `test_tracing.py` — spans created with correct names/attributes, propagation inject/extract round-trips
- `test_logging.py` — OTel handler attaches to logger, trace context appears in log records
- `test_middleware.py` — HTTP metrics recorded with correct labels, trace context extracted from incoming requests
- `test_provider.py` — setup/shutdown lifecycle, Prometheus exporter registered, OTLP exporter configured

**Approach:**
- Use OTel `InMemoryMetricReader` and `InMemorySpanExporter` for assertions — no external collector needed
- Mock import errors to test the "packages not installed" path
- Follow existing test patterns (pytest, pytest-asyncio, unittest.mock)
