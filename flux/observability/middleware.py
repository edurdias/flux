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
