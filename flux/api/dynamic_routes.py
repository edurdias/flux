"""Dynamic workflow registration (`/workflows/dynamic`).

Agent-only by construction: when authentication is enabled the endpoint
accepts nothing but **execution tokens** — the credential a running
workflow already holds — so only code executing inside Flux can author
dynamic workflows; there is no API-key/OIDC path to drift into human
authoring through a misconfigured role. The per-principal namespace is
derived server-side from the token's subject, never from the request.

Part of the ``flux.api`` route modules; see
docs/specs/2026-07-15-dynamic-workflows-spec.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException

from flux.config import Configuration
from flux.security.dependencies import get_identity
from flux.security.identity import FluxIdentity
from flux.utils import get_logger

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class DynamicRoutesMixin:
    def _register_dynamic_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        @api.post("/workflows/dynamic")
        async def register_dynamic_workflow(
            body: dict = Body(...),
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Register agent-authored workflow source into the caller's
            ``dyn-*`` namespace, stamped with the isolation runner."""
            from flux.dynamic_workflows import (
                DynamicRegistrationError,
                namespace_for_subject,
                register,
            )

            config = Configuration.get().settings.dynamic_workflows
            if not config.enabled:
                # Indistinguishable from a nonexistent route: the feature is
                # opt-in and its absence should not be probeable.
                raise HTTPException(status_code=404, detail="Not Found")

            namespace = namespace_for_subject(identity.subject)

            if auth_config.enabled:
                # Agent-only by construction: nothing but execution tokens.
                if identity.metadata.get("token_type") != "execution":
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "Dynamic workflow registration accepts only "
                            "execution tokens (in-workflow callers)"
                        ),
                    )
                if auth_service is None or not await auth_service.is_authorized(
                    identity,
                    f"workflow:{namespace}:*:register",
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "forbidden",
                            "missing_permission": f"workflow:{namespace}:*:register",
                        },
                    )

            source = body.get("source")
            if not source or not isinstance(source, str):
                raise HTTPException(
                    status_code=400,
                    detail="body must contain 'source': the workflow source as a string",
                )

            try:
                import asyncio

                result = await asyncio.to_thread(
                    register,
                    source.encode(),
                    subject=identity.subject,
                    config=config,
                )
            except DynamicRegistrationError as e:
                # Structured, actionable rejection for the authoring agent.
                raise HTTPException(
                    status_code=422,
                    detail={"error": "rejected", "message": str(e)},
                )
            return result
