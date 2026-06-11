"""Admin secrets / configs / agents routes (`/admin/secrets*`, `/admin/configs*`, `/admin/agents*`, `/agents/*/sessions`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException

from flux.secret_managers import SecretManager
from flux.security.dependencies import require_permission
from flux.security.identity import FluxIdentity
from flux.utils import get_logger
from flux.api.schemas import (
    SecretRequest,
    SecretResponse,
    ConfigRequest,
    AgentSessionSummaryResponse,
    AgentSessionListResponse,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class AdminRoutesMixin:
    def _register_admin_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        @api.get("/admin/secrets")
        async def admin_list_secrets(
            identity: FluxIdentity = Depends(require_permission("admin:secrets:read")),
        ):
            try:
                logger.info("Admin API: Listing all secrets")
                # List all secrets (names only for security)
                secret_manager = SecretManager.current()
                try:
                    # Use the new all() method to get all secret names
                    secret_names = secret_manager.all()
                    logger.info(f"Admin API: Successfully retrieved {len(secret_names)} secrets")
                    return secret_names
                except Exception as ex:
                    logger.error(f"Error listing secrets: {str(ex)}")
                    raise HTTPException(status_code=500, detail=f"Error listing secrets: {str(ex)}")
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(f"Error listing secrets: {str(ex)}")
                raise HTTPException(status_code=500, detail=str(ex))

        @api.get("/admin/secrets/{name}")
        async def admin_get_secret(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:secrets:read")),
        ):
            try:
                logger.info(f"Admin API: Getting secret '{name}'")

                # Get secret value
                secret_manager = SecretManager.current()
                try:
                    result = await secret_manager.get([name])
                    logger.info(f"Admin API: Successfully retrieved secret '{name}'")
                    return SecretResponse(name=name, value=result[name])
                except ValueError:
                    logger.warning(f"Admin API: Secret not found: '{name}'")
                    raise HTTPException(status_code=404, detail=f"Secret not found: {name}")
                except Exception as ex:
                    logger.error(f"Admin API: Error retrieving secret '{name}': {str(ex)}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error retrieving secret: {str(ex)}",
                    )
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(f"Admin API: Error in admin_get_secret for '{name}': {str(ex)}")
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/secrets")
        async def admin_create_or_update_secret(
            secret: SecretRequest = Body(...),
            identity: FluxIdentity = Depends(require_permission("admin:secrets:manage")),
        ):
            try:
                logger.info(f"Admin API: Creating/updating secret '{secret.name}'")

                # Save secret
                secret_manager = SecretManager.current()
                try:
                    secret_manager.save(secret.name, secret.value)
                    logger.info(f"Admin API: Successfully saved secret '{secret.name}'")
                    return {
                        "status": "success",
                        "message": f"Secret '{secret.name}' saved successfully",
                    }
                except Exception as ex:
                    logger.error(f"Admin API: Error saving secret '{secret.name}': {str(ex)}")
                    raise HTTPException(status_code=500, detail=f"Error saving secret: {str(ex)}")
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(
                    f"Admin API: Error in admin_create_or_update_secret for '{secret.name}': {str(ex)}",
                )
                raise HTTPException(status_code=500, detail=str(ex))

        @api.delete("/admin/secrets/{name}")
        async def admin_delete_secret(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:secrets:manage")),
        ):
            try:
                logger.info(f"Admin API: Deleting secret '{name}'")

                # Remove secret
                secret_manager = SecretManager.current()
                try:
                    secret_manager.remove(name)
                    logger.info(f"Admin API: Successfully deleted secret '{name}'")
                    return {"status": "success", "message": f"Secret '{name}' deleted successfully"}
                except Exception as ex:
                    logger.error(f"Admin API: Error deleting secret '{name}': {str(ex)}")
                    raise HTTPException(status_code=500, detail=f"Error deleting secret: {str(ex)}")
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(f"Admin API: Error in admin_delete_secret for '{name}': {str(ex)}")
                raise HTTPException(status_code=500, detail=str(ex))

        # Config API

        @api.get("/admin/configs")
        async def admin_list_configs(
            identity: FluxIdentity = Depends(require_permission("config:*:read")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                return manager.all()
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.get("/admin/configs/{name}")
        async def admin_get_config(
            name: str,
            identity: FluxIdentity = Depends(require_permission("config:*:read")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                result = await manager.get([name])
                return {"name": name, "value": result[name]}
            except ValueError:
                raise HTTPException(status_code=404, detail=f"Config not found: {name}")
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/configs")
        async def admin_create_or_update_config(
            config_req: ConfigRequest = Body(...),
            identity: FluxIdentity = Depends(require_permission("config:*:manage")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                manager.save(config_req.name, config_req.value)
                return {
                    "status": "success",
                    "message": f"Config '{config_req.name}' saved successfully",
                }
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.delete("/admin/configs/{name}")
        async def admin_delete_config(
            name: str,
            identity: FluxIdentity = Depends(require_permission("config:*:manage")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                manager.remove(name)
                return {
                    "status": "success",
                    "message": f"Config '{name}' deleted successfully",
                }
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/configs/batch")
        async def admin_batch_configs(
            keys: list[str] = Body(...),
            identity: FluxIdentity = Depends(require_permission("config:*:read")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                result = await manager.get(keys)
                return result
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/secrets/batch")
        async def admin_batch_secrets(
            keys: list[str] = Body(...),
            identity: FluxIdentity = Depends(require_permission("admin:secrets:read")),
        ):
            try:
                secret_manager = SecretManager.current()
                result = await secret_manager.get(keys)
                return result
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        # Agent API

        @api.get("/admin/agents")
        async def admin_list_agents(
            identity: FluxIdentity = Depends(require_permission("agent:*:read")),
        ):
            from flux.agents.manager import AgentManager

            try:
                manager = AgentManager.current()
                agents = manager.list()
                return [a.model_dump() for a in agents]
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.get("/admin/agents/{name}")
        async def admin_get_agent(
            name: str,
            identity: FluxIdentity = Depends(require_permission("agent:*:read")),
        ):
            from flux.agents.manager import AgentManager

            try:
                manager = AgentManager.current()
                agent_def = manager.get(name)
                return agent_def.model_dump()
            except ValueError:
                raise HTTPException(status_code=404, detail=f"Agent not found: {name}")
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/agents")
        async def admin_create_agent(
            agent_data: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("agent:*:create")),
        ):
            from flux.agents.manager import AgentManager
            from flux.agents.types import AgentDefinition

            try:
                definition = AgentDefinition(**agent_data)
                if definition.requires_code_upload_permission():
                    from flux.security.dependencies import _get_auth_service

                    upload_auth_service = _get_auth_service()
                    if upload_auth_service is not None:
                        has_perm = await upload_auth_service.is_authorized(
                            identity,
                            "workflow:*:*:register",
                        )
                        if not has_perm:
                            raise HTTPException(
                                status_code=403,
                                detail="tools_file/workflow_file/skills_dir bundles require workflow:*:*:register permission",
                            )
                manager = AgentManager.current()
                manager.create(definition)
                return {
                    "status": "success",
                    "message": f"Agent '{definition.name}' created successfully",
                }
            except HTTPException:
                raise
            except ValueError as ex:
                raise HTTPException(status_code=409, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.put("/admin/agents/{name}")
        async def admin_update_agent(
            name: str,
            agent_data: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("agent:*:update")),
        ):
            from flux.agents.manager import AgentManager
            from flux.agents.types import AgentDefinition

            try:
                agent_data["name"] = name
                definition = AgentDefinition(**agent_data)
                if definition.requires_code_upload_permission():
                    from flux.security.dependencies import _get_auth_service

                    upload_auth_service = _get_auth_service()
                    if upload_auth_service is not None:
                        has_perm = await upload_auth_service.is_authorized(
                            identity,
                            "workflow:*:*:register",
                        )
                        if not has_perm:
                            raise HTTPException(
                                status_code=403,
                                detail="tools_file/workflow_file/skills_dir bundles require workflow:*:*:register permission",
                            )
                manager = AgentManager.current()
                manager.update(definition)
                return {
                    "status": "success",
                    "message": f"Agent '{name}' updated successfully",
                }
            except HTTPException:
                raise
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.delete("/admin/agents/{name}")
        async def admin_delete_agent(
            name: str,
            identity: FluxIdentity = Depends(require_permission("agent:*:delete")),
        ):
            from flux.agents.manager import AgentManager

            try:
                manager = AgentManager.current()
                manager.delete(name)
                return {
                    "status": "success",
                    "message": f"Agent '{name}' deleted successfully",
                }
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        # Agent Sessions API

        def _list_agent_sessions(
            agent: str | None,
            state: str | None,
            limit: int,
            offset: int,
        ) -> AgentSessionListResponse:
            from flux.domain import ExecutionState
            from flux.models import AgentSessionModel, ExecutionContextModel

            state_filter: ExecutionState | None = None
            if state:
                try:
                    state_filter = ExecutionState(state.upper())
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid state '{state}'. Valid states: "
                        + ", ".join([s.value for s in ExecutionState]),
                    )

            db = self._get_db_session()
            try:
                query = db.query(AgentSessionModel, ExecutionContextModel).join(
                    ExecutionContextModel,
                    AgentSessionModel.execution_id == ExecutionContextModel.execution_id,
                )
                if agent is not None:
                    query = query.filter(AgentSessionModel.agent_name == agent)
                if state_filter is not None:
                    query = query.filter(ExecutionContextModel.state == state_filter)

                total = query.count()
                rows = (
                    query.order_by(AgentSessionModel.started_at.desc())
                    .offset(offset)
                    .limit(limit)
                    .all()
                )

                sessions = [
                    AgentSessionSummaryResponse(
                        execution_id=s.execution_id,
                        agent_name=s.agent_name,
                        state=ex.state.value,
                        started_at=s.started_at.isoformat() if s.started_at else None,
                        workflow_namespace=ex.workflow_namespace,
                        workflow_name=ex.workflow_name,
                        current_worker=ex.worker_name,
                    )
                    for s, ex in rows
                ]
                return AgentSessionListResponse(
                    sessions=sessions,
                    total=total,
                    limit=limit,
                    offset=offset,
                )
            finally:
                db.close()

        @api.get("/agents/sessions", response_model=AgentSessionListResponse)
        async def agents_sessions_list(
            agent: str | None = None,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(require_permission("agent:*:read")),
        ):
            """List agent sessions across all agents, optionally filtered."""
            return _list_agent_sessions(agent, state, limit, offset)

        @api.get(
            "/agents/{name}/sessions",
            response_model=AgentSessionListResponse,
        )
        async def agent_sessions_list_one(
            name: str,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(require_permission("agent:*:read")),
        ):
            """List sessions for one agent."""
            return _list_agent_sessions(name, state, limit, offset)
