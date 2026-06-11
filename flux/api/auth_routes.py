"""Authentication / authorization introspection routes (`/auth/*`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request

from flux.catalogs import WorkflowCatalog
from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import FluxIdentity


class AuthRoutesMixin:
    def _register_auth_routes(
        self,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        @api.get("/auth/permissions")
        async def auth_permissions(
            workflow: str | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                catalog = WorkflowCatalog.create()
                if workflow:
                    from flux.catalogs import resolve_workflow_ref as _resolve_perm

                    _perm_ns, _perm_name = _resolve_perm(workflow)
                    wf = catalog.get(_perm_ns, _perm_name)
                    meta = wf.metadata or {} if hasattr(wf, "metadata") else {}
                    perms = [f"workflow:{wf.namespace}:{wf.name}:read"]
                    perms.extend(
                        auth_service._collect_required_permissions(
                            namespace=wf.namespace,
                            workflow_name=wf.name,
                            workflow_metadata=meta,
                        ),
                    )
                    return perms
                result = {}
                for wf in catalog.all():
                    meta = wf.metadata or {} if hasattr(wf, "metadata") else {}
                    perms = [f"workflow:{wf.namespace}:{wf.name}:read"]
                    perms.extend(
                        auth_service._collect_required_permissions(
                            namespace=wf.namespace,
                            workflow_name=wf.name,
                            workflow_metadata=meta,
                        ),
                    )
                    result[f"{wf.namespace}/{wf.name}"] = perms
                return result
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/auth/test-token")
        @limiter.limit("10/minute")
        async def auth_test_token(
            request: Request,
            body: dict,
            identity: FluxIdentity = Depends(require_permission("admin:*")),
        ):
            try:
                token = body.get("token")
                if not token:
                    return {"valid": False, "error": "Missing 'token' in request body"}
                tested_identity = await auth_service.authenticate(token)
                permissions = await auth_service.resolve_permissions(tested_identity)
                return {
                    "valid": True,
                    "subject": tested_identity.subject,
                    "roles": sorted(tested_identity.roles),
                    "permissions": sorted(permissions),
                }
            except Exception:
                return {"valid": False, "error": "Invalid or expired token"}
