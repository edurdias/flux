# OpenTelemetry Observability Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in OpenTelemetry observability (metrics, tracing, logging) to Flux with Prometheus `/metrics` endpoint and OTLP push export.

**Architecture:** A new `flux/observability/` package provides setup/shutdown lifecycle, 14 metric instruments, span decorators with W3C cross-process propagation, and an additive OTel log handler. All helpers are no-ops when disabled. Integration with existing code is via single function calls — no OTel imports in business logic.

**Tech Stack:** opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp, opentelemetry-exporter-prometheus, opentelemetry-instrumentation-fastapi, opentelemetry-instrumentation-httpx, opentelemetry-instrumentation-logging

**Spec:** `docs/superpowers/specs/2026-03-12-opentelemetry-observability-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `flux/observability/__init__.py` | Public API: `setup()`, `shutdown()`, `is_enabled()`, re-exports |
| `flux/observability/config.py` | `ObservabilityConfig` Pydantic model |
| `flux/observability/provider.py` | Initialize/shutdown MeterProvider, TracerProvider, LoggerProvider |
| `flux/observability/metrics.py` | 14 metric instruments + recording helper functions |
| `flux/observability/tracing.py` | `@traced()` decorator, `start_span()` context manager, propagation helpers |
| `flux/observability/middleware.py` | FastAPI middleware for HTTP metrics + auto trace context extraction |
| `flux/observability/logging.py` | OTel log handler that attaches to existing `"flux"` logger |
| `tests/flux/observability/__init__.py` | Test package marker |
| `tests/flux/observability/test_config.py` | Config model tests |
| `tests/flux/observability/test_provider.py` | Setup/shutdown lifecycle tests |
| `tests/flux/observability/test_metrics.py` | Metric instrument + recording tests |
| `tests/flux/observability/test_tracing.py` | Span creation + propagation tests |
| `tests/flux/observability/test_middleware.py` | HTTP middleware tests |
| `tests/flux/observability/test_logging.py` | Log handler + trace correlation tests |

### Modified Files

| File | Change |
|------|--------|
| `flux/config.py` | Add `ObservabilityConfig` import + `observability` field on `FluxConfig` |
| `flux/server.py` | 5 touch points: setup, middleware, execution metrics, worker metrics, shutdown |
| `flux/worker.py` | 3 touch points: trace extraction, task metrics, checkpoint counter |
| `flux/task.py` | 2 touch points: task spans + duration, retry counter + span events |
| `pyproject.toml` | Add OTel dependencies as optional + `observability` extras group |

---

## Chunk 1: Foundation (Config + Provider + Dependencies)

### Task 1: Add OTel dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add OTel packages as optional dependencies**

Add after line 33 (`textual = "^1.0.0"`):

```toml
opentelemetry-api = {version = "^1.28", optional = true}
opentelemetry-sdk = {version = "^1.28", optional = true}
opentelemetry-exporter-otlp = {version = "^1.28", optional = true}
opentelemetry-exporter-prometheus = {version = "^0.49b0", optional = true}
opentelemetry-instrumentation-fastapi = {version = "^0.49b0", optional = true}
opentelemetry-instrumentation-httpx = {version = "^0.49b0", optional = true}
opentelemetry-instrumentation-logging = {version = "^0.49b0", optional = true}
```

Update the `[tool.poetry.extras]` section (line 117-118) to:

```toml
[tool.poetry.extras]
postgresql = ["psycopg2-binary"]
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

- [ ] **Step 2: Install dependencies**

Run: `poetry install --extras observability`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "chore: add OpenTelemetry optional dependencies"
```

---

### Task 2: Create ObservabilityConfig

**Files:**
- Create: `flux/observability/__init__.py`
- Create: `flux/observability/config.py`
- Modify: `flux/config.py`
- Test: `tests/flux/observability/__init__.py`
- Test: `tests/flux/observability/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/observability/__init__.py` (empty file).

Create `tests/flux/observability/test_config.py`:

```python
"""Tests for observability configuration."""

from flux.observability.config import ObservabilityConfig


class TestObservabilityConfig:
    def test_defaults(self):
        config = ObservabilityConfig()
        assert config.enabled is False
        assert config.service_name == "flux"
        assert config.otlp_endpoint is None
        assert config.otlp_protocol == "grpc"
        assert config.prometheus_enabled is True
        assert config.trace_sample_rate == 1.0
        assert config.metric_export_interval == 60
        assert config.resource_attributes == {}

    def test_custom_values(self):
        config = ObservabilityConfig(
            enabled=True,
            service_name="flux-prod",
            otlp_endpoint="http://collector:4317",
            otlp_protocol="http/protobuf",
            prometheus_enabled=False,
            trace_sample_rate=0.5,
            metric_export_interval=30,
            resource_attributes={"env": "production"},
        )
        assert config.enabled is True
        assert config.service_name == "flux-prod"
        assert config.otlp_endpoint == "http://collector:4317"
        assert config.otlp_protocol == "http/protobuf"
        assert config.prometheus_enabled is False
        assert config.trace_sample_rate == 0.5
        assert config.metric_export_interval == 30
        assert config.resource_attributes == {"env": "production"}

    def test_flux_config_includes_observability(self):
        from flux.config import FluxConfig

        config = FluxConfig()
        assert hasattr(config, "observability")
        assert config.observability.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/flux/observability/test_config.py -v`
Expected: FAIL (import errors)

- [ ] **Step 3: Implement config**

Create `flux/observability/__init__.py`:

```python
"""Flux observability package — OpenTelemetry metrics, tracing, and logging."""
```

Create `flux/observability/config.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class ObservabilityConfig(BaseModel):
    """Configuration for OpenTelemetry observability."""

    enabled: bool = Field(default=False, description="Enable observability")
    service_name: str = Field(default="flux", description="OTel service name")

    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP collector endpoint (e.g. http://localhost:4317)",
    )
    otlp_protocol: str = Field(
        default="grpc",
        description="OTLP protocol: grpc or http/protobuf",
    )
    prometheus_enabled: bool = Field(
        default=True,
        description="Enable Prometheus /metrics endpoint",
    )

    trace_sample_rate: float = Field(
        default=1.0,
        description="Trace sampling rate (0.0 to 1.0)",
    )

    metric_export_interval: int = Field(
        default=60,
        description="OTLP metric export interval in seconds",
    )

    resource_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Additional OTel resource attributes",
    )
```

Modify `flux/config.py` — add import and field to `FluxConfig`:

Add after existing imports (line 15):

```python
from flux.observability.config import ObservabilityConfig
```

Add to `FluxConfig` class after line 162 (`scheduling: SchedulingConfig = ...`):

```python
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/flux/observability/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add flux/observability/ tests/flux/observability/ flux/config.py
git commit -m "feat(observability): add ObservabilityConfig and wire into FluxConfig"
```

---

### Task 3: Create Provider (setup/shutdown lifecycle)

**Files:**
- Create: `flux/observability/provider.py`
- Modify: `flux/observability/__init__.py`
- Test: `tests/flux/observability/test_provider.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/observability/test_provider.py`:

```python
"""Tests for observability provider setup/shutdown lifecycle."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.observability.config import ObservabilityConfig


class TestProviderLifecycle:
    def test_setup_sets_enabled_flag(self):
        from flux.observability import is_enabled, setup, shutdown

        assert is_enabled() is False
        config = ObservabilityConfig(enabled=True)
        setup(config)
        assert is_enabled() is True
        shutdown()
        assert is_enabled() is False

    def test_setup_disabled_is_noop(self):
        from flux.observability import is_enabled, setup

        config = ObservabilityConfig(enabled=False)
        setup(config)
        assert is_enabled() is False

    def test_setup_missing_packages_raises(self):
        from flux.observability.provider import _check_dependencies

        with patch.dict("sys.modules", {"opentelemetry": None}):
            with pytest.raises(ImportError, match="observability"):
                _check_dependencies()

    def test_shutdown_without_setup_is_safe(self):
        from flux.observability import shutdown

        shutdown()  # Should not raise

    def test_get_meter_returns_meter(self):
        from flux.observability import get_meter, setup, shutdown

        config = ObservabilityConfig(enabled=True)
        setup(config)
        meter = get_meter("test")
        assert meter is not None
        shutdown()

    def test_get_tracer_returns_tracer(self):
        from flux.observability import get_tracer, setup, shutdown

        config = ObservabilityConfig(enabled=True)
        setup(config)
        tracer = get_tracer("test")
        assert tracer is not None
        shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/flux/observability/test_provider.py -v`
Expected: FAIL (import errors)

- [ ] **Step 3: Implement provider**

Create `flux/observability/provider.py`:

```python
"""OTel provider initialization and shutdown."""

from __future__ import annotations

import logging

from flux.observability.config import ObservabilityConfig

logger = logging.getLogger("flux.observability")

_meter_provider = None
_tracer_provider = None
_logger_provider = None


def _check_dependencies():
    """Verify OTel packages are installed."""
    try:
        import opentelemetry  # noqa: F401
    except ImportError:
        raise ImportError(
            "OpenTelemetry packages not installed. "
            "Install with: pip install flux-core[observability]"
        )


def setup_providers(config: ObservabilityConfig):
    """Initialize MeterProvider, TracerProvider, and LoggerProvider."""
    global _meter_provider, _tracer_provider, _logger_provider

    _check_dependencies()

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    resource_attrs = {"service.name": config.service_name}
    resource_attrs.update(config.resource_attributes)
    resource = Resource.create(resource_attrs)

    # Metrics
    readers = []
    if config.prometheus_enabled:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        readers.append(PrometheusMetricReader())

    if config.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        otlp_exporter = OTLPMetricExporter(endpoint=config.otlp_endpoint)
        readers.append(
            PeriodicExportingMetricReader(
                otlp_exporter,
                export_interval_millis=config.metric_export_interval * 1000,
            )
        )

    _meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(_meter_provider)

    # Tracing
    sampler = TraceIdRatioBased(config.trace_sample_rate)
    _tracer_provider = TracerProvider(resource=resource, sampler=sampler)

    span_exporters = []
    if config.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        span_exporters.append(OTLPSpanExporter(endpoint=config.otlp_endpoint))

    if span_exporters:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        for exporter in span_exporters:
            _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(_tracer_provider)

    # Logging
    if config.otlp_endpoint:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        _logger_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPLogExporter(endpoint=config.otlp_endpoint)
        _logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(_logger_provider)

    logger.info(
        f"Observability initialized (prometheus={config.prometheus_enabled}, "
        f"otlp={'enabled' if config.otlp_endpoint else 'disabled'}, "
        f"sample_rate={config.trace_sample_rate})"
    )


def shutdown_providers():
    """Flush and shut down all providers."""
    global _meter_provider, _tracer_provider, _logger_provider

    if _meter_provider:
        _meter_provider.shutdown()
        _meter_provider = None

    if _tracer_provider:
        _tracer_provider.shutdown()
        _tracer_provider = None

    if _logger_provider:
        _logger_provider.shutdown()
        _logger_provider = None

    logger.info("Observability shut down")


def get_meter_provider():
    return _meter_provider


def get_tracer_provider():
    return _tracer_provider
```

Update `flux/observability/__init__.py`:

```python
"""Flux observability package — OpenTelemetry metrics, tracing, and logging."""

from __future__ import annotations

from flux.observability.config import ObservabilityConfig

_enabled = False


def is_enabled() -> bool:
    """Check if observability is active."""
    return _enabled


def setup(config: ObservabilityConfig) -> None:
    """Initialize observability if enabled."""
    global _enabled
    if not config.enabled:
        return

    from flux.observability.provider import setup_providers

    setup_providers(config)
    _enabled = True


def shutdown() -> None:
    """Shut down observability providers."""
    global _enabled
    if not _enabled:
        return

    from flux.observability.provider import shutdown_providers

    shutdown_providers()
    _enabled = False


def get_meter(name: str):
    """Get an OTel Meter. Returns a no-op meter if disabled."""
    if not _enabled:
        from opentelemetry import metrics

        return metrics.get_meter(name)
    from opentelemetry import metrics

    return metrics.get_meter(name)


def get_tracer(name: str):
    """Get an OTel Tracer. Returns a no-op tracer if disabled."""
    if not _enabled:
        from opentelemetry import trace

        return trace.get_tracer(name)
    from opentelemetry import trace

    return trace.get_tracer(name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/flux/observability/test_provider.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add flux/observability/ tests/flux/observability/
git commit -m "feat(observability): add provider setup/shutdown lifecycle"
```

---

## Chunk 2: Metrics + Middleware

### Task 4: Create Metric Instruments

**Files:**
- Create: `flux/observability/metrics.py`
- Test: `tests/flux/observability/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/observability/test_metrics.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/flux/observability/test_metrics.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Implement metrics**

Create `flux/observability/metrics.py`:

```python
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

    def record_http_request(
        self, method: str, endpoint: str, status_code: int, duration: float
    ):
        attrs = {"method": method, "endpoint": endpoint, "status_code": str(status_code)}
        self.http_requests.add(1, attrs)
        self.http_duration.record(duration, {"method": method, "endpoint": endpoint})

    def record_checkpoint(self, workflow_name: str):
        self.checkpoints.add(1, {"workflow_name": workflow_name})

    def record_execution_queued(self):
        self.queue_depth.add(1)

    def record_execution_claimed(self):
        self.queue_depth.add(-1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/flux/observability/test_metrics.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add flux/observability/metrics.py tests/flux/observability/test_metrics.py
git commit -m "feat(observability): add 14 metric instruments with recording helpers"
```

---

### Task 5: Create HTTP Middleware

**Files:**
- Create: `flux/observability/middleware.py`
- Test: `tests/flux/observability/test_middleware.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/observability/test_middleware.py`:

```python
"""Tests for observability HTTP middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from flux.observability.metrics import FluxMetrics
from flux.observability.middleware import MetricsMiddleware


@pytest.fixture
def app_with_middleware():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = FluxMetrics(provider.get_meter("flux-test"))

    app = FastAPI()
    app.add_middleware(MetricsMiddleware, metrics=metrics)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    @app.get("/error")
    async def error_endpoint():
        raise ValueError("test error")

    return app, reader


class TestMetricsMiddleware:
    def test_records_request_count(self, app_with_middleware):
        app, reader = app_with_middleware
        client = TestClient(app, raise_server_exceptions=False)

        client.get("/test")

        data = reader.get_metrics_data()
        metric_names = []
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    metric_names.append(m.name)

        assert "flux_http_requests_total" in metric_names
        assert "flux_http_request_duration_seconds" in metric_names

    def test_records_status_code(self, app_with_middleware):
        app, reader = app_with_middleware
        client = TestClient(app, raise_server_exceptions=False)

        client.get("/test")

        data = reader.get_metrics_data()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "flux_http_requests_total":
                        for dp in m.data.data_points:
                            assert dp.attributes["status_code"] == "200"

    def test_records_error_status(self, app_with_middleware):
        app, reader = app_with_middleware
        client = TestClient(app, raise_server_exceptions=False)

        client.get("/error")

        data = reader.get_metrics_data()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "flux_http_requests_total":
                        for dp in m.data.data_points:
                            assert dp.attributes["status_code"] == "500"

    def test_skips_metrics_endpoint(self, app_with_middleware):
        app, reader = app_with_middleware
        client = TestClient(app, raise_server_exceptions=False)

        # Add a /metrics route
        @app.get("/metrics")
        async def metrics_endpoint():
            return "metrics"

        client.get("/metrics")

        data = reader.get_metrics_data()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "flux_http_requests_total":
                        for dp in m.data.data_points:
                            assert dp.attributes.get("endpoint") != "/metrics"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/flux/observability/test_middleware.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Implement middleware**

Create `flux/observability/middleware.py`:

```python
"""FastAPI middleware for HTTP metrics."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from flux.observability.metrics import FluxMetrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records HTTP request count and duration metrics."""

    def __init__(self, app, metrics: FluxMetrics):
        super().__init__(app)
        self.metrics = metrics

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            raise
        finally:
            duration = time.monotonic() - start
            endpoint = request.url.path
            method = request.method
            self.metrics.record_http_request(method, endpoint, status_code, duration)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/flux/observability/test_middleware.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add flux/observability/middleware.py tests/flux/observability/test_middleware.py
git commit -m "feat(observability): add HTTP metrics middleware"
```

---

## Chunk 3: Tracing + Logging

### Task 6: Create Tracing Module

**Files:**
- Create: `flux/observability/tracing.py`
- Test: `tests/flux/observability/test_tracing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/observability/test_tracing.py`:

```python
"""Tests for observability tracing."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter

from flux.observability import tracing


@pytest.fixture
def span_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.shutdown()
    provider.shutdown()


class TestTracing:
    def test_start_span_creates_span(self, span_exporter):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("test.span") as span:
            span.set_attribute("flux.workflow.name", "my_wf")

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.span"
        assert spans[0].attributes["flux.workflow.name"] == "my_wf"

    def test_traced_decorator(self, span_exporter):
        @tracing.traced("test.decorated")
        def my_func():
            return 42

        result = my_func()

        assert result == 42
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.decorated"

    @pytest.mark.asyncio
    async def test_traced_decorator_async(self, span_exporter):
        @tracing.traced("test.async_decorated")
        async def my_async_func():
            return 99

        result = await my_async_func()

        assert result == 99
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.async_decorated"

    def test_inject_extract_roundtrip(self, span_exporter):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("parent"):
            carrier = tracing.inject_trace_context()

        assert "traceparent" in carrier

        ctx = tracing.extract_trace_context(carrier)
        assert ctx is not None

    def test_inject_without_span_returns_empty(self):
        carrier = tracing.inject_trace_context()
        assert carrier == {} or "traceparent" not in carrier
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/flux/observability/test_tracing.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Implement tracing**

Create `flux/observability/tracing.py`:

```python
"""Span decorators, context managers, and W3C propagation helpers."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable

from opentelemetry import context, trace
from opentelemetry.propagate import extract, inject


def traced(span_name: str, attributes: dict[str, Any] | None = None) -> Callable:
    """Decorator that wraps a function in an OTel span."""

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = trace.get_tracer("flux")
                with tracer.start_as_current_span(span_name) as span:
                    if attributes:
                        for k, v in attributes.items():
                            span.set_attribute(k, v)
                    return await func(*args, **kwargs)

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                tracer = trace.get_tracer("flux")
                with tracer.start_as_current_span(span_name) as span:
                    if attributes:
                        for k, v in attributes.items():
                            span.set_attribute(k, v)
                    return func(*args, **kwargs)

            return sync_wrapper

    return decorator


def inject_trace_context() -> dict[str, str]:
    """Inject current trace context into a carrier dict (W3C format)."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict[str, str]) -> context.Context:
    """Extract trace context from a carrier dict."""
    return extract(carrier)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/flux/observability/test_tracing.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add flux/observability/tracing.py tests/flux/observability/test_tracing.py
git commit -m "feat(observability): add tracing decorators and W3C propagation"
```

---

### Task 7: Create Logging Integration

**Files:**
- Create: `flux/observability/logging.py`
- Test: `tests/flux/observability/test_logging.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/observability/test_logging.py`:

```python
"""Tests for observability logging integration."""

from __future__ import annotations

import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter

from flux.observability.logging import setup_log_handler, teardown_log_handler


@pytest.fixture
def tracer_setup():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield provider, exporter
    exporter.shutdown()
    provider.shutdown()


class TestLoggingIntegration:
    def test_handler_attaches_to_logger(self):
        test_logger = logging.getLogger("flux.test_attach")
        initial_count = len(test_logger.handlers)

        handler = setup_log_handler(test_logger)
        assert len(test_logger.handlers) == initial_count + 1

        teardown_log_handler(test_logger, handler)
        assert len(test_logger.handlers) == initial_count

    def test_log_includes_trace_context(self, tracer_setup):
        provider, _ = tracer_setup
        test_logger = logging.getLogger("flux.test_trace_ctx")
        test_logger.setLevel(logging.DEBUG)

        handler = setup_log_handler(test_logger)

        records = []
        capture_handler = logging.Handler()
        capture_handler.emit = lambda record: records.append(record)
        test_logger.addHandler(capture_handler)

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("test.span") as span:
            test_logger.info("test message")
            expected_trace_id = format(span.get_span_context().trace_id, "032x")

        assert len(records) >= 1
        record = records[0]
        assert hasattr(record, "otelTraceID")
        assert record.otelTraceID == expected_trace_id

        teardown_log_handler(test_logger, handler)
        test_logger.removeHandler(capture_handler)

    def test_log_without_span_has_empty_trace(self):
        test_logger = logging.getLogger("flux.test_no_span")
        test_logger.setLevel(logging.DEBUG)

        handler = setup_log_handler(test_logger)

        records = []
        capture_handler = logging.Handler()
        capture_handler.emit = lambda record: records.append(record)
        test_logger.addHandler(capture_handler)

        test_logger.info("no span message")

        assert len(records) >= 1
        record = records[0]
        assert hasattr(record, "otelTraceID")
        assert record.otelTraceID == "0" * 32

        teardown_log_handler(test_logger, handler)
        test_logger.removeHandler(capture_handler)

    def test_teardown_without_setup_is_safe(self):
        test_logger = logging.getLogger("flux.test_safe_teardown")
        teardown_log_handler(test_logger, None)  # Should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/flux/observability/test_logging.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Implement logging**

Create `flux/observability/logging.py`:

```python
"""OTel log handler that attaches to existing logger hierarchy."""

from __future__ import annotations

import logging

from opentelemetry import trace


class OTelTraceLogHandler(logging.Handler):
    """Injects trace/span IDs into log records."""

    def emit(self, record: logging.LogRecord) -> None:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            record.otelTraceID = format(ctx.trace_id, "032x")
            record.otelSpanID = format(ctx.span_id, "016x")
        else:
            record.otelTraceID = "0" * 32
            record.otelSpanID = "0" * 16


class OTelTraceLogFilter(logging.Filter):
    """Adds trace/span IDs to log records as attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            record.otelTraceID = format(ctx.trace_id, "032x")
            record.otelSpanID = format(ctx.span_id, "016x")
        else:
            record.otelTraceID = "0" * 32
            record.otelSpanID = "0" * 16
        return True


def setup_log_handler(logger: logging.Logger) -> OTelTraceLogHandler:
    """Add OTel trace context handler to a logger."""
    handler = OTelTraceLogHandler()
    handler.addFilter(OTelTraceLogFilter())
    logger.addHandler(handler)
    return handler


def teardown_log_handler(logger: logging.Logger, handler: OTelTraceLogHandler | None) -> None:
    """Remove OTel handler from a logger."""
    if handler:
        logger.removeHandler(handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/flux/observability/test_logging.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add flux/observability/logging.py tests/flux/observability/test_logging.py
git commit -m "feat(observability): add OTel log handler with trace correlation"
```

---

## Chunk 4: Wire Into Existing Code

### Task 8: Wire Metrics Singleton Into Provider

**Files:**
- Modify: `flux/observability/__init__.py`
- Modify: `flux/observability/provider.py`

This task adds a module-level `FluxMetrics` singleton that the rest of the codebase accesses via `flux.observability.get_metrics()`. When disabled, returns `None`.

- [ ] **Step 1: Update `flux/observability/__init__.py`**

Add after existing functions:

```python
def get_metrics():
    """Get the FluxMetrics singleton. Returns None if disabled."""
    if not _enabled:
        return None
    from flux.observability.provider import get_flux_metrics

    return get_flux_metrics()
```

- [ ] **Step 2: Update `flux/observability/provider.py`**

Add module-level variable and getter. After `_logger_provider = None` add:

```python
_flux_metrics = None
```

At the end of `setup_providers()`, before the logger.info line, add:

```python
    global _flux_metrics
    from opentelemetry import metrics as otel_metrics

    from flux.observability.metrics import FluxMetrics

    meter = otel_metrics.get_meter("flux")
    _flux_metrics = FluxMetrics(meter)
```

Add getter function:

```python
def get_flux_metrics():
    return _flux_metrics
```

In `shutdown_providers()`, add:

```python
    global _flux_metrics
    _flux_metrics = None
```

- [ ] **Step 3: Commit**

```bash
git add flux/observability/__init__.py flux/observability/provider.py
git commit -m "feat(observability): add FluxMetrics singleton accessor"
```

---

### Task 9: Integrate Into Server

**Files:**
- Modify: `flux/server.py`

This task adds 5 touch points to server.py. Each is a single function call to the observability module.

- [ ] **Step 1: Add setup in `__init__`**

After the heartbeat/cache state setup (around line 208), add:

```python
        from flux.observability import setup as setup_observability
        from flux.observability.config import ObservabilityConfig

        obs_config = Configuration.get().settings.observability
        setup_observability(obs_config)
```

- [ ] **Step 2: Add middleware and /metrics route in `_create_api`**

After CORS middleware (around line 580), add:

```python
        from flux.observability import get_metrics, is_enabled

        if is_enabled():
            from flux.observability.middleware import MetricsMiddleware

            metrics = get_metrics()
            if metrics:
                api.add_middleware(MetricsMiddleware, metrics=metrics)

            # Prometheus /metrics endpoint
            obs_config = Configuration.get().settings.observability
            if obs_config.prometheus_enabled:
                from prometheus_client import REGISTRY, generate_latest

                @api.get("/metrics")
                async def metrics_endpoint():
                    from starlette.responses import Response

                    return Response(
                        content=generate_latest(REGISTRY),
                        media_type="text/plain; version=0.0.4; charset=utf-8",
                    )
```

- [ ] **Step 3: Add execution metrics in `_create_execution`**

In `_create_execution` method (around line 388), after the execution is saved, add:

```python
        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_execution_started(workflow_name)
            m.record_execution_queued()
```

- [ ] **Step 4: Add worker metrics**

In the worker register endpoint (around line 1006), after successful registration, add:

```python
                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_worker_connected(registration.name)
```

In `_disconnect_worker` method (around line 454), add:

```python
        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_worker_disconnected(name, "disconnect")
```

In the worker claim endpoint (around line 1184), after successful claim, add:

```python
                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_execution_claimed()
```

- [ ] **Step 5: Add shutdown in lifespan**

In the lifespan context manager (around line 561), add after the existing shutdown calls:

```python
            from flux.observability import shutdown as shutdown_observability

            await asyncio.get_event_loop().run_in_executor(None, shutdown_observability)
```

- [ ] **Step 6: Run all tests to verify no regressions**

Run: `poetry run pytest -x -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add flux/server.py
git commit -m "feat(observability): wire metrics into server (5 touch points)"
```

---

### Task 10: Integrate Into Worker and Task

**Files:**
- Modify: `flux/worker.py`
- Modify: `flux/task.py`
- Modify: `flux/server.py` (schedule trigger)

- [ ] **Step 1: Add trace extraction and task metrics to worker**

In `_handle_execution_scheduled` (around line 254), at the start of the method, extract trace context from the SSE event:

```python
        from flux.observability import get_metrics
        from flux.observability.tracing import extract_trace_context

        trace_ctx = {}
        try:
            event_data = json.loads(evt.data) if isinstance(evt.data, str) else {}
            trace_ctx = event_data.get("trace_context", {})
        except Exception:
            pass

        parent_ctx = extract_trace_context(trace_ctx) if trace_ctx else None
```

In `_execute_workflow` (around line 303), wrap execution in a span and record metrics:

After the execution completes (around line 354 where `execution_time` is calculated), add:

```python
        m = get_metrics()
        if m:
            m.record_workflow_completed(
                request.workflow.name,
                "completed" if not failed else "failed",
                execution_time,
            )
            m.record_execution_ended(request.workflow.name)
```

In `_send_checkpoint` (around line 387), after successful checkpoint, add:

```python
        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_checkpoint(context.workflow_name)
```

- [ ] **Step 2: Add task spans and metrics to task.py**

In the `__call__` method of the Task class (around line 96), wrap the task execution section in timing and record metrics after completion:

After task completes or fails, add:

```python
        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_task_completed(
                context.workflow_name,
                self.name,
                "completed" if not failed else "failed",
                task_duration,
            )
```

In `__handle_retry` (around line 362), at the start of each retry attempt, add:

```python
        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_task_retry(context.workflow_name, self.name)
```

- [ ] **Step 3: Add schedule trigger metrics**

In `_trigger_scheduled_workflow` in server.py (around line 529), after successful trigger add:

```python
            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_schedule_trigger(schedule.name, "success")
```

In the exception handler of the same method, add:

```python
            m = get_metrics()
            if m:
                m.record_schedule_trigger(schedule.name, "failure")
```

- [ ] **Step 4: Add trace context injection to SSE dispatch**

In server.py's SSE connect endpoint (around line 1089), when yielding the `execution_scheduled` event, inject trace context into the event data:

```python
                                    from flux.observability.tracing import inject_trace_context

                                    event_data = json.loads(data_payload)
                                    event_data["trace_context"] = inject_trace_context()
                                    data_payload = json.dumps(event_data)
```

- [ ] **Step 5: Run all tests**

Run: `poetry run pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Run pre-commit**

Run: `poetry run pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 7: Commit**

```bash
git add flux/worker.py flux/task.py flux/server.py
git commit -m "feat(observability): wire tracing and metrics into worker, task, and scheduler"
```

---

## Chunk 5: Final Verification

### Task 11: Run Full Test Suite and Pre-commit

- [ ] **Step 1: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass (no regressions)

- [ ] **Step 2: Run pre-commit**

Run: `poetry run pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 3: Verify observability disabled by default**

Verify that existing tests pass without OTel packages imported — the observability module is only loaded when `enabled=True`.

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(observability): address pre-commit and test issues"
```
