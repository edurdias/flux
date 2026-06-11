"""RBAC admin routes (`/admin/roles*`, `/admin/principals*`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


from fastapi import Depends
from fastapi import HTTPException

from flux.security.dependencies import require_permission
from flux.security.identity import FluxIdentity
from flux.utils import get_logger
from flux.api.schemas import (
    RoleRequest,
    RoleUpdateRequest,
    RoleCloneRequest,
    APIKeyRequest,
    PrincipalCreateRequest,
    PrincipalUpdateRequest,
    RoleGrantRequest,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class RbacRoutesMixin:
    def _register_rbac_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        # ===========================================
        # Auth & Admin: Roles
        # ===========================================

        @api.get("/admin/roles")
        async def admin_list_roles(
            identity: FluxIdentity = Depends(require_permission("admin:roles:read")),
        ):
            try:
                roles = await auth_service.list_roles()
                return [
                    {
                        "name": r.name,
                        "permissions": r.permissions,
                        "built_in": r.built_in,
                    }
                    for r in roles
                ]
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/admin/roles/{name}")
        async def admin_get_role(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:roles:read")),
        ):
            try:
                role = await auth_service.get_role(name)
                if not role:
                    raise HTTPException(status_code=404, detail=f"Role '{name}' not found")
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/roles")
        async def admin_create_role(
            request: RoleRequest,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                role = await auth_service.create_role(request.name, request.permissions)
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except ValueError as e:
                msg = str(e)
                if "already exists" in msg.lower():
                    raise HTTPException(status_code=409, detail=msg)
                raise HTTPException(status_code=400, detail=msg)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.patch("/admin/roles/{name}")
        async def admin_update_role(
            name: str,
            request: RoleUpdateRequest,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                role = await auth_service.update_role(
                    name,
                    add_permissions=request.add_permissions,
                    remove_permissions=request.remove_permissions,
                )
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/roles/{name}")
        async def admin_delete_role(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                await auth_service.delete_role(name)
                return {"status": "success", "message": f"Role '{name}' deleted"}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/roles/{name}/clone")
        async def admin_clone_role(
            name: str,
            request: RoleCloneRequest,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                role = await auth_service.clone_role(name, request.new_name)
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except ValueError as e:
                msg = str(e)
                if "already exists" in msg.lower():
                    raise HTTPException(status_code=409, detail=msg)
                raise HTTPException(status_code=400, detail=msg)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # ===========================================
        # Auth & Admin: Principals
        # ===========================================

        @api.get("/admin/principals")
        async def admin_list_principals(
            type: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:read")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principals = registry.list_all(type=type)
                return [
                    {
                        "id": str(p.id),
                        "subject": p.subject,
                        "type": p.type,
                        "external_issuer": p.external_issuer,
                        "display_name": p.display_name,
                        "enabled": p.enabled,
                        "roles": registry.get_roles(p.id),
                    }
                    for p in principals
                ]
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/admin/principals/{subject}")
        async def admin_get_principal(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:read")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal and issuer is None:
                    principal = registry.find(subject, "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                return {
                    "id": str(principal.id),
                    "subject": principal.subject,
                    "type": principal.type,
                    "external_issuer": principal.external_issuer,
                    "display_name": principal.display_name,
                    "enabled": principal.enabled,
                    "roles": registry.get_roles(principal.id),
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals", status_code=201)
        async def admin_create_principal(
            request: PrincipalCreateRequest,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                external_issuer = request.external_issuer or (
                    "flux" if request.type == "service_account" else "flux"
                )
                principal = registry.create(
                    type=request.type,
                    subject=request.subject,
                    external_issuer=external_issuer,
                    display_name=request.display_name,
                )
                for role in request.roles:
                    registry.assign_role(principal.id, role)
                return {
                    "id": str(principal.id),
                    "subject": principal.subject,
                    "type": principal.type,
                    "external_issuer": principal.external_issuer,
                    "display_name": principal.display_name,
                    "enabled": principal.enabled,
                    "roles": request.roles,
                }
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.patch("/admin/principals/{subject}")
        async def admin_update_principal(
            subject: str,
            request: PrincipalUpdateRequest,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                if request.display_name is not None:
                    registry.update_metadata(principal.id, display_name=request.display_name)
                if request.enabled is not None:
                    registry.set_enabled(principal.id, request.enabled)
                updated = registry.get(principal.id) or principal
                return {
                    "id": str(updated.id),
                    "subject": updated.subject,
                    "display_name": updated.display_name,
                    "enabled": updated.enabled,
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/principals/{subject}", status_code=200)
        async def admin_delete_principal(
            subject: str,
            issuer: str | None = None,
            force: bool = False,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.delete(principal.id, force=force)
                return {"status": "success", "message": f"Principal '{subject}' deleted"}
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/roles")
        async def admin_grant_principal_role(
            subject: str,
            request: RoleGrantRequest,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.assign_role(principal.id, request.role, assigned_by=identity.subject)
                return {"status": "success", "role": request.role}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/principals/{subject}/roles/{role_name}")
        async def admin_revoke_principal_role(
            subject: str,
            role_name: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.revoke_role(principal.id, role_name)
                return {"status": "success", "role": role_name}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/enable")
        async def admin_enable_principal(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.set_enabled(principal.id, True)
                return {"status": "success", "subject": subject, "enabled": True}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/disable")
        async def admin_disable_principal(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.set_enabled(principal.id, False)
                return {"status": "success", "subject": subject, "enabled": False}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/keys", status_code=201)
        async def admin_create_principal_key(
            subject: str,
            request: APIKeyRequest,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from datetime import timedelta

                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                if principal.type != "service_account":
                    raise HTTPException(
                        status_code=400,
                        detail="API keys can only be created for service_account principals",
                    )
                expires = (
                    timedelta(days=request.expires_in_days) if request.expires_in_days else None
                )
                key_plaintext = await auth_service.create_api_key(
                    principal.id,
                    request.name,
                    expires,
                )
                return {"key": key_plaintext}
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/admin/principals/{subject}/keys")
        async def admin_list_principal_keys(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:read")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                keys = await auth_service.list_api_keys(principal.id)
                return [
                    {
                        "name": k.name,
                        "prefix": k.key_prefix,
                        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                        "created_at": k.created_at.isoformat(),
                    }
                    for k in keys
                ]
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/principals/{subject}/keys/{key_name}")
        async def admin_revoke_principal_key(
            subject: str,
            key_name: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                await auth_service.revoke_api_key(principal.id, key_name)
                return {"status": "success", "message": f"Key '{key_name}' revoked"}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
