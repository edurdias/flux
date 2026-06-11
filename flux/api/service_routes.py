"""Workflow service routes (`/services*`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio
from typing import Any

from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse

from flux.catalogs import WorkflowCatalog
from flux.context_managers import ContextManager
from flux.errors import WorkflowNotFoundError
from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import ANONYMOUS, FluxIdentity
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.utils import get_logger
from flux.api.schemas import (
    SERVICE_NAME_RE,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class ServiceRoutesMixin:
    def _register_service_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        # ===========================================
        # Services: CRUD
        # ===========================================

        @api.post("/services", status_code=201)
        async def create_service(
            request: Request,
            identity: FluxIdentity = Depends(require_permission("service:*:manage")),
        ):
            from json import JSONDecodeError

            from flux.service_store import ServiceStore

            try:
                body = await request.json()
            except (JSONDecodeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid JSON body")

            name = body.get("name")
            if not name or not isinstance(name, str):
                raise HTTPException(
                    status_code=400,
                    detail="Service name is required and must be a string",
                )
            if not SERVICE_NAME_RE.match(name):
                raise HTTPException(
                    status_code=400,
                    detail="Service name must be lowercase alphanumeric with hyphens/underscores (e.g. 'my-service-1')",
                )

            for field in ("namespaces", "workflows", "exclusions"):
                val = body.get(field, [])
                if not isinstance(val, list):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list",
                    )
                if not all(isinstance(x, str) for x in val):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list of strings",
                    )

            mcp_val = body.get("mcp_enabled", False)
            if not isinstance(mcp_val, bool):
                raise HTTPException(status_code=400, detail="mcp_enabled must be a boolean")

            try:
                store = ServiceStore()
                svc = store.create(
                    name=name,
                    namespaces=body.get("namespaces", []),
                    workflows=body.get("workflows", []),
                    exclusions=body.get("exclusions", []),
                    mcp_enabled=mcp_val,
                )
                return {
                    "id": svc.id,
                    "name": svc.name,
                    "namespaces": svc.namespaces,
                    "workflows": svc.workflows,
                    "exclusions": svc.exclusions,
                    "mcp_enabled": svc.mcp_enabled,
                    "created_at": svc.created_at.isoformat() if svc.created_at else None,
                    "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                }
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except Exception as e:
                logger.error(f"Error creating service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error creating service: {str(e)}")

        @api.get("/services")
        async def list_services(
            identity: FluxIdentity = Depends(require_permission("service:*:read")),
        ):
            from flux.service_store import ServiceStore

            try:
                store = ServiceStore()
                services = store.list()
                return [
                    {
                        "id": svc.id,
                        "name": svc.name,
                        "namespaces": svc.namespaces,
                        "workflows": svc.workflows,
                        "exclusions": svc.exclusions,
                        "mcp_enabled": svc.mcp_enabled,
                        "created_at": svc.created_at.isoformat() if svc.created_at else None,
                        "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                    }
                    for svc in services
                ]
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing services: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing services: {str(e)}")

        @api.get("/services/{service_name}")
        async def get_service(
            service_name: str,
            identity: FluxIdentity = Depends(require_permission("service:*:read")),
        ):
            from flux.service_store import ServiceStore
            from flux.service_resolver import ServiceResolver, CollisionError

            try:
                store = ServiceStore()
                svc = store.get(service_name)
                if not svc:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Service '{service_name}' not found",
                    )

                catalog = WorkflowCatalog.create()
                resolver = ServiceResolver(catalog, store)

                result: dict[str, Any] = {
                    "id": svc.id,
                    "name": svc.name,
                    "namespaces": svc.namespaces,
                    "workflows": svc.workflows,
                    "exclusions": svc.exclusions,
                    "mcp_enabled": svc.mcp_enabled,
                    "created_at": svc.created_at.isoformat() if svc.created_at else None,
                    "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                }

                try:
                    endpoints = resolver.resolve(service_name)
                    result["endpoints"] = [
                        {
                            "name": wf.name,
                            "namespace": wf.namespace,
                            "version": wf.version,
                            "input_schema": (wf.metadata or {}).get("input_schema"),
                            "description": (wf.metadata or {}).get("description"),
                        }
                        for wf in endpoints.values()
                    ]
                except CollisionError as ce:
                    result["endpoints"] = []
                    result["collision_warning"] = str(ce)

                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error getting service: {str(e)}")

        @api.put("/services/{service_name}")
        async def update_service(
            service_name: str,
            request: Request,
            identity: FluxIdentity = Depends(require_permission("service:*:manage")),
        ):
            from json import JSONDecodeError

            from flux.service_store import ServiceStore, ServiceNotFoundError

            try:
                body = await request.json()
            except (JSONDecodeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid JSON body")

            for field in (
                "add_namespaces",
                "add_workflows",
                "add_exclusions",
                "remove_namespaces",
                "remove_workflows",
                "remove_exclusions",
            ):
                val = body.get(field)
                if val is not None and not isinstance(val, list):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list",
                    )
                if val is not None and not all(isinstance(x, str) for x in val):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list of strings",
                    )

            mcp_val = body.get("mcp_enabled")
            if mcp_val is not None and not isinstance(mcp_val, bool):
                raise HTTPException(status_code=400, detail="mcp_enabled must be a boolean")

            try:
                store = ServiceStore()
                svc = store.update(
                    name=service_name,
                    add_namespaces=body.get("add_namespaces"),
                    add_workflows=body.get("add_workflows"),
                    add_exclusions=body.get("add_exclusions"),
                    remove_namespaces=body.get("remove_namespaces"),
                    remove_workflows=body.get("remove_workflows"),
                    remove_exclusions=body.get("remove_exclusions"),
                    mcp_enabled=mcp_val,
                )
                return {
                    "id": svc.id,
                    "name": svc.name,
                    "namespaces": svc.namespaces,
                    "workflows": svc.workflows,
                    "exclusions": svc.exclusions,
                    "mcp_enabled": svc.mcp_enabled,
                    "created_at": svc.created_at.isoformat() if svc.created_at else None,
                    "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                }
            except HTTPException:
                raise
            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except Exception as e:
                logger.error(f"Error updating service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error updating service: {str(e)}")

        @api.delete("/services/{service_name}")
        async def delete_service(
            service_name: str,
            identity: FluxIdentity = Depends(require_permission("service:*:manage")),
        ):
            from flux.service_store import ServiceStore, ServiceNotFoundError

            try:
                store = ServiceStore()
                store.delete(service_name)
                return {"detail": f"Service '{service_name}' deleted"}
            except HTTPException:
                raise
            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except Exception as e:
                logger.error(f"Error deleting service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error deleting service: {str(e)}")

        @api.get("/services/{service_name}/mcp/tools")
        async def service_mcp_info(
            service_name: str,
            identity: FluxIdentity = Depends(require_permission("service:*:read")),
        ):
            from flux.service_store import ServiceStore
            from flux.service_resolver import ServiceResolver, CollisionError

            try:
                store = ServiceStore()
                svc = store.get(service_name)
                if not svc or not svc.mcp_enabled:
                    raise HTTPException(
                        status_code=404,
                        detail=f"MCP not enabled for service '{service_name}'",
                    )

                catalog = WorkflowCatalog.create()
                resolver = ServiceResolver(catalog, store)

                try:
                    endpoints = resolver.resolve(service_name)
                except CollisionError:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Endpoint collision in service '{service_name}'",
                    )

                tools = []
                for wf in endpoints.values():
                    name = wf.name
                    schema = (wf.metadata or {}).get("input_schema")
                    desc = (wf.metadata or {}).get("description", "")
                    tools.extend(
                        [
                            {
                                "name": name,
                                "description": f"Run {name} synchronously. {desc}".strip(),
                                "input_schema": schema,
                            },
                            {"name": f"{name}_async", "description": f"Run {name} asynchronously."},
                            {
                                "name": f"resume_{name}",
                                "description": f"Resume paused {name} synchronously.",
                            },
                            {
                                "name": f"resume_{name}_async",
                                "description": f"Resume paused {name} asynchronously.",
                            },
                            {
                                "name": f"status_{name}",
                                "description": f"Check {name} execution status.",
                            },
                        ],
                    )

                return {
                    "service": service_name,
                    "mcp_enabled": True,
                    "tools_url": f"/services/{service_name}/mcp/tools",
                    "tools": tools,
                    "tool_count": len(tools),
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting service MCP info: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error getting service MCP info: {str(e)}",
                )

        # ===========================================
        # Services: Execution endpoints
        # ===========================================

        def _service_detailed(ctx_dict, service_name, workflow_name, **extra):
            result = {
                "execution_id": ctx_dict.get("execution_id"),
                "state": ctx_dict.get("state"),
                "output": ctx_dict.get("output"),
                "namespace": ctx_dict.get("workflow_namespace"),
                "workflow": ctx_dict.get("workflow_name"),
            }
            result.update(extra)
            return result

        def _map_service_response(ctx_dict, service_name, workflow_name, mode, detailed):
            state = ctx_dict.get("state", "")
            exec_id = ctx_dict.get("execution_id")

            if mode == "async":
                return JSONResponse(
                    status_code=202,
                    content=_service_detailed(
                        ctx_dict,
                        service_name,
                        workflow_name,
                        status_url=f"/services/{service_name}/{workflow_name}/status/{exec_id}",
                    ),
                )

            if state == "COMPLETED":
                if detailed:
                    return JSONResponse(
                        status_code=200,
                        content=_service_detailed(ctx_dict, service_name, workflow_name),
                    )
                return JSONResponse(status_code=200, content=ctx_dict.get("output"))

            if state == "FAILED":
                content = _service_detailed(ctx_dict, service_name, workflow_name)
                content["error"] = str(ctx_dict.get("output", "Workflow failed"))
                return JSONResponse(status_code=500, content=content)

            if state == "PAUSED":
                return JSONResponse(
                    status_code=202,
                    content=_service_detailed(
                        ctx_dict,
                        service_name,
                        workflow_name,
                        resume_url=f"/services/{service_name}/{workflow_name}/resume/{exec_id}",
                    ),
                )

            return JSONResponse(status_code=200, content=ctx_dict)

        @api.post("/services/{service_name}/{workflow_name}")
        @api.post("/services/{service_name}/{workflow_name}/{mode}")
        async def service_run_workflow(
            service_name: str,
            workflow_name: str,
            input: Any = Body(None),
            mode: str = "sync",
            detailed: bool = False,
            version: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_resolver import (
                CollisionError,
                ServiceResolver,
                WorkflowNotInServiceError,
            )
            from flux.service_store import ServiceNotFoundError, ServiceStore

            try:
                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                resolver = ServiceResolver(WorkflowCatalog.create(), ServiceStore())
                wf_info = resolver.find(service_name, workflow_name)
                namespace = wf_info.namespace

                if auth_service is not None and auth_config.enabled:
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        wf_info.name,
                        wf_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                ctx = self._create_execution(namespace, wf_info.name, input, version)
                manager = ContextManager.create()

                if identity and identity != ANONYMOUS and auth_config.enabled:
                    from flux.security.execution_token import mint_execution_token

                    principal_issuer = (identity.metadata or {}).get("issuer", "flux")
                    exec_token = mint_execution_token(
                        subject=identity.subject,
                        principal_issuer=principal_issuer,
                        execution_id=ctx.execution_id,
                        on_behalf_of=identity.subject,
                    )
                    token_session = self._get_db_session()
                    try:
                        from flux.models import ExecutionContextModel as _ECM_SVC

                        exec_row = token_session.get(_ECM_SVC, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            token_session.commit()
                    finally:
                        token_session.close()

                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(ctx.execution_id, asyncio.Event())

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                if mode == "stream":
                    self._progress_buffers[ctx.execution_id] = asyncio.Queue(maxsize=10000)
                    return EventSourceResponse(
                        self._stream_execution_events(ctx, manager, detailed),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                ctx_dict = dto.model_dump() if hasattr(dto, "model_dump") else dto.dict()
                return _map_service_response(ctx_dict, service_name, workflow_name, mode, detailed)

            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except WorkflowNotInServiceError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow '{workflow_name}' not found in service '{service_name}'",
                )
            except CollisionError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error running workflow via service {service_name}/{workflow_name}: {str(e)}",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error running workflow via service: {str(e)}",
                )

        @api.post("/services/{service_name}/{workflow_name}/resume/{execution_id}")
        @api.post("/services/{service_name}/{workflow_name}/resume/{execution_id}/{mode}")
        async def service_resume_workflow(
            service_name: str,
            workflow_name: str,
            execution_id: str,
            input: Any = Body(None),
            mode: str = "sync",
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_resolver import (
                CollisionError,
                ServiceResolver,
                WorkflowNotInServiceError,
            )
            from flux.service_store import ServiceNotFoundError, ServiceStore

            try:
                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                resolver = ServiceResolver(WorkflowCatalog.create(), ServiceStore())
                wf_info = resolver.find(service_name, workflow_name)
                namespace = wf_info.namespace

                if auth_service is not None and auth_config.enabled:
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        wf_info.name,
                        wf_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                manager = ContextManager.create()
                ctx = manager.get(execution_id)

                if ctx is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution context with ID {execution_id} not found.",
                    )

                if (
                    getattr(ctx, "workflow_namespace", None) != wf_info.namespace
                    or getattr(ctx, "workflow_name", None) != wf_info.name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{execution_id}' does not belong to workflow {wf_info.namespace}/{wf_info.name}",
                    )

                if identity and identity != ANONYMOUS and auth_config.enabled:
                    from flux.security.execution_token import mint_execution_token

                    principal_issuer = (identity.metadata or {}).get("issuer", "flux")
                    exec_token = mint_execution_token(
                        subject=identity.subject,
                        principal_issuer=principal_issuer,
                        execution_id=ctx.execution_id,
                        on_behalf_of=identity.subject,
                    )
                    resume_token_session = self._get_db_session()
                    try:
                        from flux.models import ExecutionContextModel as _ECM_SVC2

                        exec_row = resume_token_session.get(_ECM_SVC2, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            resume_token_session.commit()
                    finally:
                        resume_token_session.close()

                if ctx.has_finished:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot resume a finished execution.",
                    )

                if not ctx.is_paused:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Cannot resume an execution in state '{ctx.state.value}'; "
                        "it is not paused.",
                    )

                ctx.start_resuming(input)
                manager.save(ctx)

                from flux.observability import get_metrics as _get_resume_metrics

                _rm = _get_resume_metrics()
                if _rm:
                    _rm.record_resume_queued(ctx.workflow_namespace, ctx.workflow_name)

                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(ctx.execution_id, asyncio.Event())

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                if mode == "stream":
                    self._progress_buffers[ctx.execution_id] = asyncio.Queue(maxsize=10000)
                    return EventSourceResponse(
                        self._stream_execution_events(ctx, manager, detailed),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                ctx_dict = dto.model_dump() if hasattr(dto, "model_dump") else dto.dict()
                return _map_service_response(ctx_dict, service_name, workflow_name, mode, detailed)

            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except WorkflowNotInServiceError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow '{workflow_name}' not found in service '{service_name}'",
                )
            except CollisionError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error resuming workflow via service {service_name}/{workflow_name}: {str(e)}",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error resuming workflow via service: {str(e)}",
                )

        @api.get("/services/{service_name}/{workflow_name}/status/{execution_id}")
        async def service_workflow_status(
            service_name: str,
            workflow_name: str,
            execution_id: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_resolver import (
                CollisionError,
                ServiceResolver,
                WorkflowNotInServiceError,
            )
            from flux.service_store import ServiceNotFoundError, ServiceStore

            try:
                resolver = ServiceResolver(WorkflowCatalog.create(), ServiceStore())
                wf_info = resolver.find(service_name, workflow_name)
                namespace = wf_info.namespace

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{wf_info.name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{wf_info.name}:read'",
                        )

                manager = ContextManager.create()
                context = manager.get(execution_id)
                if context is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{execution_id}' not found",
                    )

                if (
                    getattr(context, "workflow_namespace", None) != wf_info.namespace
                    or getattr(context, "workflow_name", None) != wf_info.name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{execution_id}' does not belong to workflow {wf_info.namespace}/{wf_info.name}",
                    )

                dto = ExecutionContextDTO.from_domain(context)
                summary = dto.summary()
                return JSONResponse(
                    status_code=200,
                    content=_service_detailed(summary, service_name, workflow_name),
                )

            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except WorkflowNotInServiceError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow '{workflow_name}' not found in service '{service_name}'",
                )
            except CollisionError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error checking status via service {service_name}/{workflow_name}: {str(e)}",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error checking workflow status via service: {str(e)}",
                )
