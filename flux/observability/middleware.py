"""Pure ASGI middleware for HTTP metrics."""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Receive, Scope, Send

from flux.observability.metrics import FluxMetrics


class MetricsMiddleware:
    """Records HTTP request count and duration metrics.

    Implemented as a pure ASGI middleware to avoid BaseHTTPMiddleware's
    buffering issues with streaming/SSE responses.
    """

    def __init__(self, app: ASGIApp, metrics: FluxMetrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            self.metrics.record_http_request(method, path, status_code, duration)
