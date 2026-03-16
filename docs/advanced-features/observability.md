# Observability

Flux provides built-in observability through [OpenTelemetry](https://opentelemetry.io/) — metrics, distributed tracing, and log correlation. The feature is **opt-in** with zero overhead when disabled.

## Installation

Observability requires optional dependencies:

```bash
pip install flux-core[observability]
```

## Configuration

Add the `[flux.observability]` section to your `flux.toml`:

```toml
[flux.observability]
enabled = true
service_name = "flux"
prometheus_enabled = true
otlp_endpoint = "http://localhost:4317"  # Optional: OTLP collector
trace_sample_rate = 1.0
metric_export_interval = 60
```

Or use environment variables:

```bash
FLUX_OBSERVABILITY__ENABLED=true
FLUX_OBSERVABILITY__SERVICE_NAME=flux
FLUX_OBSERVABILITY__PROMETHEUS_ENABLED=true
FLUX_OBSERVABILITY__OTLP_ENDPOINT=http://localhost:4317
FLUX_OBSERVABILITY__TRACE_SAMPLE_RATE=1.0
FLUX_OBSERVABILITY__METRIC_EXPORT_INTERVAL=60
```

### Configuration Reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable observability |
| `service_name` | `"flux"` | OpenTelemetry service name |
| `prometheus_enabled` | `true` | Expose `/metrics` endpoint |
| `otlp_endpoint` | `null` | OTLP collector gRPC endpoint |
| `trace_sample_rate` | `1.0` | Trace sampling rate (0.0 to 1.0) |
| `metric_export_interval` | `60` | OTLP push interval in seconds |
| `resource_attributes` | `{}` | Additional OTel resource attributes |

### Behavior

- **`enabled: false`** (default) — nothing initializes, no overhead
- **`enabled: true`**, no `otlp_endpoint` — Prometheus `/metrics` only
- **`enabled: true`** with `otlp_endpoint` — both Prometheus and OTLP push

## Metrics

Flux exposes 18 metric instruments accessible via the Prometheus `/metrics` endpoint at `http://localhost:8000/metrics`.

### Workflow Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_workflow_executions_total` | Counter | `workflow_name`, `status` | Workflow executions by status (`started`, `completed`, `failed`, `cancelled`) |
| `flux_workflow_execution_duration_seconds` | Histogram | `workflow_name` | Worker-side workflow execution duration |

### Task Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_task_executions_total` | Counter | `workflow_name`, `task_name`, `status` | Task executions by status (`started`, `completed`, `failed`) |
| `flux_task_execution_duration_seconds` | Histogram | `workflow_name`, `task_name` | Per-task execution duration |
| `flux_task_retries_total` | Counter | `workflow_name`, `task_name` | Task retry attempts |

### Execution Pipeline Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_execution_queue_depth` | UpDownCounter | — | Executions waiting for workers |
| `flux_execution_schedule_to_start_seconds` | Histogram | — | Time from queued to worker claim |
| `flux_checkpoints_total` | Counter | `workflow_name` | Checkpoint events |
| `flux_checkpoint_duration_seconds` | Histogram | `workflow_name` | Checkpoint HTTP round-trip duration |

### Worker Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_workers_active` | UpDownCounter | — | Connected workers |
| `flux_worker_registrations_total` | Counter | `worker_name` | Registration events |
| `flux_worker_disconnections_total` | Counter | `worker_name`, `reason` | Disconnection events |
| `flux_worker_executions_active` | UpDownCounter | `worker_name` | Concurrent executions per worker |
| `flux_module_cache_total` | Counter | `result` | Module cache lookups (`hit`, `miss`) |

### Schedule Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_schedule_triggers_total` | Counter | `schedule_name`, `outcome` | Schedule trigger outcomes |

### HTTP Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `flux_http_requests_total` | Counter | `method`, `endpoint`, `status_code` | HTTP request count (paths normalized) |
| `flux_http_request_duration_seconds` | Histogram | `method`, `endpoint` | HTTP request latency |

## Distributed Tracing

When enabled, Flux creates spans for workflow executions, task executions, and HTTP requests. Trace context is automatically propagated between the server and workers using W3C `traceparent`/`tracestate` headers.

### Span Hierarchy

```
[Server] HTTP POST /workflows/{name}/run/async
  -- (trace context propagated via SSE event) --
  +-- [Worker] flux.workflow.execute
        +-- [Worker] flux.task.execute {task_name}
        +-- [Worker] flux.task.execute {task_name}
```

### Span Attributes

All custom attributes use the `flux.*` namespace:

- `flux.workflow.name` — Workflow name
- `flux.execution.id` — Execution ID
- `flux.task.name` — Task name
- `flux.worker.name` — Worker name

## Log Correlation

When observability is enabled, an OTel log handler is added to the root `flux` logger. Log records emitted inside an active span automatically include `otelTraceID` and `otelSpanID` attributes, allowing you to correlate logs with traces.

## Docker Compose Setup

The easiest way to run Flux with full observability is using the Docker Compose observability profile:

```bash
docker compose -f docker-compose.yml \
  -f docker/profiles/observability.yml \
  up -d
```

This starts:

| Service | URL | Purpose |
|---------|-----|---------|
| Flux Server | `http://localhost:8000` | Workflow engine with `/metrics` |
| Prometheus | `http://localhost:9090` | Metrics storage and queries |
| Grafana | `http://localhost:3000` | Dashboards (admin/admin) |
| Jaeger | `http://localhost:16686` | Trace visualization |
| OTel Collector | `localhost:4317` | Receives OTLP data |

### Example Prometheus Queries

```promql
# Workflow execution rate (per minute)
rate(flux_workflow_executions_total[5m]) * 60

# Average workflow duration
rate(flux_workflow_execution_duration_seconds_sum[5m]) / rate(flux_workflow_execution_duration_seconds_count[5m])

# Execution queue depth
flux_execution_queue_depth

# Task failure rate
rate(flux_task_executions_total{status="failed"}[5m])

# HTTP request latency (p95)
histogram_quantile(0.95, rate(flux_http_request_duration_seconds_bucket[5m]))

# Connected workers
flux_workers_active

# Execution queue depth
flux_execution_queue_depth
```

### Grafana Setup

1. Open Grafana at `http://localhost:3000` (login: admin/admin)
2. Add Prometheus data source: `http://prometheus:9090`
3. Create dashboards using the queries above

### Jaeger Setup

1. Open Jaeger at `http://localhost:16686`
2. Select service `flux` from the dropdown
3. Search for traces to see distributed execution flow
