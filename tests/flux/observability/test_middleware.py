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
        if data is None:
            # No metrics recorded means the /metrics endpoint was correctly skipped
            return
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "flux_http_requests_total":
                        for dp in m.data.data_points:
                            assert dp.attributes.get("endpoint") != "/metrics"
