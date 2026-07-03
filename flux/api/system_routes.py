"""System routes (`/metrics`, `/health`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING


from fastapi import Depends
from fastapi import Response

from flux.catalogs import WorkflowCatalog
from flux.config import Configuration
from flux.security.dependencies import require_permission
from flux.security.identity import FluxIdentity
from flux.utils import get_logger
from flux.api.schemas import (
    HealthResponse,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class SystemRoutesMixin:
    def _register_system_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        from flux.observability import is_enabled

        if is_enabled():
            # Prometheus /metrics endpoint
            obs_config = Configuration.get().settings.observability
            if obs_config.prometheus_enabled:
                from prometheus_client import REGISTRY, generate_latest

                @api.get("/metrics")
                async def metrics_endpoint(
                    identity: FluxIdentity = Depends(require_permission("admin:metrics:read")),
                ):
                    from starlette.responses import Response

                    return Response(
                        content=generate_latest(REGISTRY),
                        media_type="text/plain; version=0.0.4; charset=utf-8",
                    )

        # ===========================================
        # Health & System Endpoints
        # ===========================================

        @api.get("/ready")
        async def ready(response: Response):
            """Readiness probe: can this replica serve traffic right now?

            Separate from /health so orchestrators can distinguish "remove
            from the load balancer" (readiness, e.g. a DB blip) from "restart
            the process" (liveness). Points a database round-trip.
            """
            try:
                # The catalog construction itself opens the engine on first
                # use, so it must run inside the thread hop as well — not just
                # the health check — or a slow DB blocks the event loop.
                db_ready = await asyncio.to_thread(
                    lambda: WorkflowCatalog.create().health_check(),
                )
            except Exception:
                db_ready = False
            if not db_ready:
                response.status_code = 503
                return {"status": "not-ready", "database": False}
            return {"status": "ready", "database": True}

        @api.get("/health", response_model=HealthResponse)
        async def health(response: Response):
            """Health check endpoint."""
            try:
                logger.debug("Health check requested")

                # Check database connectivity
                catalog = WorkflowCatalog.create()
                db_healthy = catalog.health_check()

                status = "healthy" if db_healthy else "unhealthy"
                version = self._get_version()

                result = HealthResponse(
                    status=status,
                    database=db_healthy,
                    version=version,
                )

                # Status-code-only probes must fail when the DB is unreachable.
                if not db_healthy:
                    response.status_code = 503

                logger.debug(f"Health check result: {status}")
                return result

            except Exception as e:
                logger.error(f"Health check failed: {str(e)}")
                response.status_code = 503
                return HealthResponse(
                    status="unhealthy",
                    database=False,
                    version=self._get_version(),
                )
