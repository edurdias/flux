"""Execution and approval routes (`/executions*`, `/approvals`, `/workflows/*/executions`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio

from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse

from flux.catalogs import WorkflowCatalog
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionContextNotFoundError, WorkflowNotFoundError
from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import FluxIdentity
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.utils import get_logger
from flux.api.schemas import (
    _has_any_workflow_read,
    ApprovalDecideRequest,
    ExecutionSummaryResponse,
    ExecutionListResponse,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class ExecutionRoutesMixin:
    def _register_execution_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        # ===========================================
        # Execution Endpoints
        # ===========================================

        async def _check_workflow_read(identity: FluxIdentity, ns: str, name: str) -> bool:
            if not auth_config.enabled:
                return True
            if auth_service is None:
                return False
            return await auth_service.is_authorized(
                identity,
                f"workflow:{ns}:{name}:read",
            )

        @api.get("/executions", response_model=ExecutionListResponse)
        async def executions_list(
            workflow_name: str | None = None,
            namespace: str | None = None,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(require_permission("execution:*:read")),
        ):
            """List executions with optional filtering.

            ``execution:*:read`` alone is not enough to see every workflow's
            executions: like the approvals listing, results are scoped to the
            workflows the caller may read, so execution inputs/outputs never
            leak across workflow read boundaries.
            """
            try:
                logger.debug(
                    f"Listing executions (namespace: {namespace}, workflow: {workflow_name}, "
                    f"state: {state}, limit: {limit}, offset: {offset})",
                )

                from flux.domain import ExecutionState

                # Parse state if provided
                state_filter = None
                if state:
                    try:
                        state_filter = ExecutionState(state.upper())
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid state '{state}'. Valid states: "
                            + ", ".join([s.value for s in ExecutionState]),
                        )

                has_broad_read = True
                if auth_config.enabled and auth_service is not None:
                    permissions = await auth_service.resolve_permissions(identity)
                    has_broad_read = identity.has_permission("workflow:*:*:read", permissions)
                    if not has_broad_read and not _has_any_workflow_read(permissions):
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "forbidden",
                                "missing_permission": "workflow:*:*:read",
                            },
                        )

                manager = ContextManager.create()

                workflows_filter = None
                if not has_broad_read:
                    # Scoped reader: authorize per distinct workflow before
                    # the paginated query (the approvals-listing pattern), so
                    # pagination and totals stay correct.
                    candidates = manager.distinct_workflows(
                        workflow_name=workflow_name,
                        workflow_namespace=namespace,
                        state=state_filter,
                    )
                    workflows_filter = [
                        (ns, nm)
                        for ns, nm in candidates
                        if await _check_workflow_read(identity, ns, nm)
                    ]

                executions, total = manager.list(
                    workflow_name=workflow_name,
                    workflow_namespace=namespace,
                    state=state_filter,
                    limit=limit,
                    offset=offset,
                    workflows=workflows_filter,
                )

                result = ExecutionListResponse(
                    executions=[
                        ExecutionSummaryResponse(
                            execution_id=ex.execution_id,
                            workflow_id=ex.workflow_id,
                            workflow_namespace=ex.workflow_namespace,
                            workflow_name=ex.workflow_name,
                            state=ex.state.value,
                            worker_name=ex.current_worker,
                        )
                        for ex in executions
                    ],
                    total=total,
                    limit=limit,
                    offset=offset,
                    auth_filtered=workflows_filter is not None,
                )

                logger.debug(f"Found {total} executions")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing executions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing executions: {str(e)}",
                )

        @api.get("/executions/{execution_id}")
        async def execution_get(
            execution_id: str,
            detailed: bool = False,
            mode: str = "sync",
            identity: FluxIdentity = Depends(require_permission("execution:*:read")),
        ):
            """Get execution by ID.

            ``mode=stream`` attaches to the execution's live event stream
            instead of returning a one-shot snapshot — used to follow an
            execution after it resumes out of band (e.g. after an approval
            decision posted to the approve/reject routes).
            """
            try:
                if mode not in ("sync", "stream"):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync' or 'stream'.",
                    )

                logger.debug(f"Fetching execution: {execution_id}")

                manager = ContextManager.create()
                ctx = manager.get(execution_id)

                # The flat execution:*:read grant does not bypass workflow
                # read boundaries: the detailed DTO carries the workflow's
                # inputs and outputs.
                if not await _check_workflow_read(
                    identity,
                    ctx.workflow_namespace,
                    ctx.workflow_name,
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "forbidden",
                            "missing_permission": (
                                f"workflow:{ctx.workflow_namespace}:{ctx.workflow_name}:read"
                            ),
                        },
                    )

                if mode == "stream":
                    self._execution_events.setdefault(ctx.execution_id, asyncio.Event())
                    self._progress_buffers.setdefault(
                        ctx.execution_id,
                        asyncio.Queue(maxsize=10000),
                    )
                    return EventSourceResponse(
                        self._stream_execution_events(
                            ctx,
                            manager,
                            detailed,
                            emit_initial=True,
                        ),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                result = dto.summary() if not detailed else dto

                logger.debug(f"Found execution {execution_id} in state: {ctx.state.value}")
                return result

            except ExecutionContextNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Execution '{execution_id}' not found",
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error retrieving execution: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving execution: {str(e)}",
                )

        # ===========================================
        # Approval Endpoints (read-side)
        # ===========================================

        from flux.utils import parse_iso8601_duration

        def _approval_to_dict(r) -> dict:
            return {
                "approval_id": r.id,
                "execution_id": r.execution_id,
                "task_call_id": r.task_call_id,
                "workflow_namespace": r.workflow_namespace,
                "workflow_name": r.workflow_name,
                "task_name": r.task_name,
                "status": r.status.value,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                "approver": (
                    {"subject": r.approver_subject, "provider": r.approver_provider}
                    if r.approver_subject
                    else None
                ),
                "reason": r.reason,
                "scope": r.scope or "call",
            }

        # (workflow-read scoping uses the shared _check_workflow_read helper
        # defined with the execution endpoints above.)

        @api.get("/approvals")
        async def list_approvals_cross_execution(
            status: str | None = Query("pending"),
            execution_id: str | None = Query(None),
            workflow_namespace: str | None = Query(None),
            workflow_name: str | None = Query(None),
            task_name: str | None = Query(None),
            age_min: str | None = Query(None),
            limit: int = Query(20, ge=1, le=200),
            offset: int = Query(0, ge=0),
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.approvals import ApprovalManager
            from flux.models import ApprovalStatus

            if status == "all":
                parsed_status = None
            elif status:
                try:
                    parsed_status = ApprovalStatus(status)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status: {status}",
                    )
            else:
                parsed_status = ApprovalStatus.PENDING

            parsed_age = None
            if age_min:
                try:
                    parsed_age = parse_iso8601_duration(age_min)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid age_min: {age_min}",
                    )

            has_broad_read = True
            if auth_config.enabled and auth_service is not None:
                permissions = await auth_service.resolve_permissions(identity)
                has_broad_read = identity.has_permission("workflow:*:*:read", permissions)
                if not has_broad_read and not _has_any_workflow_read(permissions):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "forbidden",
                            "missing_permission": "workflow:*:*:read",
                        },
                    )

            mgr = ApprovalManager()

            if has_broad_read:
                rows = mgr.list(
                    status=parsed_status,
                    execution_id=execution_id,
                    workflow_namespace=workflow_namespace,
                    workflow_name=workflow_name,
                    task_name=task_name,
                    age_min=parsed_age,
                    limit=limit,
                    offset=offset,
                )
                total = mgr.count(
                    status=parsed_status,
                    execution_id=execution_id,
                    workflow_namespace=workflow_namespace,
                    workflow_name=workflow_name,
                    task_name=task_name,
                    age_min=parsed_age,
                )
                return {
                    "approvals": [_approval_to_dict(r) for r in rows],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "auth_filtered": False,
                }

            # Scoped reader: resolve which workflows the caller may read
            # *before* querying approvals, so the query stays bounded and
            # paginates correctly. The distinct-workflows scan is bounded by
            # the workflow catalog, not the approvals table.
            candidate_workflows = mgr.distinct_workflows(
                status=parsed_status,
                execution_id=execution_id,
                workflow_namespace=workflow_namespace,
                workflow_name=workflow_name,
                task_name=task_name,
                age_min=parsed_age,
            )
            authorized_workflows = [
                (ns, nm)
                for ns, nm in candidate_workflows
                if await _check_workflow_read(identity, ns, nm)
            ]
            rows = mgr.list(
                status=parsed_status,
                execution_id=execution_id,
                workflow_namespace=workflow_namespace,
                workflow_name=workflow_name,
                task_name=task_name,
                age_min=parsed_age,
                workflows=authorized_workflows,
                limit=limit,
                offset=offset,
            )
            total = mgr.count(
                status=parsed_status,
                execution_id=execution_id,
                workflow_namespace=workflow_namespace,
                workflow_name=workflow_name,
                task_name=task_name,
                age_min=parsed_age,
                workflows=authorized_workflows,
            )
            return {
                "approvals": [_approval_to_dict(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "auth_filtered": True,
            }

        @api.get("/executions/{execution_id}/approvals")
        async def list_approvals_for_execution(
            execution_id: str,
            status: str | None = Query("pending"),
            limit: int = Query(20, ge=1, le=200),
            offset: int = Query(0, ge=0),
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.approvals import ApprovalManager
            from flux.models import ApprovalStatus

            try:
                exec_ctx = ContextManager.create().get(execution_id)
            except ExecutionContextNotFoundError:
                raise HTTPException(status_code=404, detail={"error": "not_found"})

            if not await _check_workflow_read(
                identity,
                exec_ctx.workflow_namespace,
                exec_ctx.workflow_name,
            ):
                raise HTTPException(status_code=403, detail={"error": "forbidden"})

            if status == "all":
                parsed_status = None
            elif status:
                try:
                    parsed_status = ApprovalStatus(status)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status: {status}",
                    )
            else:
                parsed_status = ApprovalStatus.PENDING

            approval_mgr = ApprovalManager()
            rows = approval_mgr.list(
                status=parsed_status,
                execution_id=execution_id,
                limit=limit,
                offset=offset,
            )
            total = approval_mgr.count(
                status=parsed_status,
                execution_id=execution_id,
            )
            return {
                "approvals": [_approval_to_dict(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

        @api.get("/executions/{execution_id}/approvals/{task_call_id:path}")
        async def get_one_approval(
            execution_id: str,
            task_call_id: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.approvals import ApprovalManager

            try:
                exec_ctx = ContextManager.create().get(execution_id)
            except ExecutionContextNotFoundError:
                raise HTTPException(status_code=404, detail={"error": "not_found"})

            if not await _check_workflow_read(
                identity,
                exec_ctx.workflow_namespace,
                exec_ctx.workflow_name,
            ):
                raise HTTPException(status_code=403, detail={"error": "forbidden"})

            row = ApprovalManager().get_by_call(execution_id, task_call_id)
            if row is None:
                raise HTTPException(status_code=404, detail={"error": "not_found"})
            return _approval_to_dict(row)

        async def _decide_approval(
            execution_id: str,
            task_call_id: str,
            identity: FluxIdentity,
            *,
            approved: bool,
            reason: str | None,
            scope: str = "call",
        ):
            """Shared implementation for the approve/reject POST routes.

            Two-stage AuthZ:
              1. ``workflow:<ns>:<wf>:read`` on the execution's workflow.
              2. ``workflow:<ns>:<wf>:task:<task>:approve`` on the approval row's task.

            The decide + WORKFLOW event append + RESUMING transition all run
            inside a single ``UnitOfWork`` so a partial failure cannot leave the
            row decided but the workflow still paused (or vice versa).
            """
            from flux.approvals import (
                ApprovalAlreadyDecided,
                ApprovalManager,
            )
            from flux.unit_of_work import UnitOfWork

            cm = ContextManager.create()

            try:
                exec_ctx = cm.get(execution_id)
            except ExecutionContextNotFoundError:
                raise HTTPException(status_code=404, detail={"error": "not_found"})

            if not await _check_workflow_read(
                identity,
                exec_ctx.workflow_namespace,
                exec_ctx.workflow_name,
            ):
                raise HTTPException(status_code=403, detail={"error": "forbidden"})

            mgr = ApprovalManager()
            row = mgr.get_by_call(execution_id, task_call_id)
            if row is None:
                raise HTTPException(status_code=404, detail={"error": "not_found"})

            required = (
                f"workflow:{row.workflow_namespace}:{row.workflow_name}"
                f":task:{row.task_name}:approve"
            )
            if auth_service is not None and auth_config.enabled:
                if not await auth_service.is_authorized(identity, required):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "forbidden",
                            "missing_permission": required,
                        },
                    )

            approver_subject = identity.subject if identity is not None else "anonymous"
            approver_provider = (
                (identity.metadata or {}).get("issuer", "flux")
                if identity is not None
                else "anonymous"
            )

            try:
                with UnitOfWork() as uow:
                    updated = mgr.decide(
                        execution_id,
                        task_call_id,
                        approver_subject=approver_subject,
                        approver_provider=approver_provider,
                        approved=approved,
                        reason=reason,
                        uow=uow,
                        scope=scope,
                    )
                    event_type = (
                        ExecutionEventType.TASK_APPROVED
                        if approved
                        else ExecutionEventType.TASK_REJECTED
                    )
                    decided_iso = updated.decided_at.isoformat() if updated.decided_at else None
                    exec_ctx.force_start_resuming()
                    exec_ctx.events.append(
                        ExecutionEvent(
                            type=event_type,
                            source_id=task_call_id,
                            name=updated.task_name,
                            value={
                                "approver": {
                                    "subject": updated.approver_subject,
                                    "provider": updated.approver_provider,
                                },
                                "reason": reason,
                                "decided_at": decided_iso,
                                "scope": updated.scope or "call",
                            },
                        ),
                    )
                    cm.save(exec_ctx, uow=uow)
                    uow.commit()
            except ApprovalAlreadyDecided as e:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "already_decided",
                        "current_status": e.current_status.value,
                        "decided_at": (e.decided_at.isoformat() if e.decided_at else None),
                    },
                )

            self._notify_next_worker()

            payload = _approval_to_dict(updated)
            payload["execution_state"] = exec_ctx.state.value
            return payload

        @api.post("/executions/{execution_id}/approvals/{task_call_id:path}/approve")
        async def approve_approval(
            execution_id: str,
            task_call_id: str,
            body: ApprovalDecideRequest | None = Body(default=None),
            identity: FluxIdentity = Depends(get_identity),
        ):
            reason = body.reason if body is not None else None
            always = body.always if body is not None else False
            return await _decide_approval(
                execution_id,
                task_call_id,
                identity,
                approved=True,
                reason=reason,
                scope="execution" if always else "call",
            )

        @api.post("/executions/{execution_id}/approvals/{task_call_id:path}/reject")
        async def reject_approval(
            execution_id: str,
            task_call_id: str,
            body: ApprovalDecideRequest | None = Body(default=None),
            identity: FluxIdentity = Depends(get_identity),
        ):
            reason = body.reason if body is not None else None
            return await _decide_approval(
                execution_id,
                task_call_id,
                identity,
                approved=False,
                reason=reason,
            )

        @api.get(
            "/workflows/{namespace}/{workflow_name}/executions",
            response_model=ExecutionListResponse,
        )
        async def workflow_executions_list_ns(
            namespace: str,
            workflow_name: str,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List executions for a specific workflow."""
            try:
                logger.debug(
                    f"Listing executions for workflow '{namespace}/{workflow_name}' "
                    f"(state: {state}, limit: {limit}, offset: {offset})",
                )

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )

                from flux.domain import ExecutionState

                catalog = WorkflowCatalog.create()
                try:
                    catalog.get(namespace, workflow_name)
                except WorkflowNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{namespace}/{workflow_name}' not found",
                    )

                state_filter = None
                if state:
                    try:
                        state_filter = ExecutionState(state.upper())
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid state '{state}'. Valid states: "
                            + ", ".join([s.value for s in ExecutionState]),
                        )

                manager = ContextManager.create()
                executions, total = manager.list(
                    workflow_name=workflow_name,
                    workflow_namespace=namespace,
                    state=state_filter,
                    limit=limit,
                    offset=offset,
                )

                result = ExecutionListResponse(
                    executions=[
                        ExecutionSummaryResponse(
                            execution_id=ex.execution_id,
                            workflow_id=ex.workflow_id,
                            workflow_namespace=ex.workflow_namespace,
                            workflow_name=ex.workflow_name,
                            state=ex.state.value,
                            worker_name=ex.current_worker,
                        )
                        for ex in executions
                    ],
                    total=total,
                    limit=limit,
                    offset=offset,
                )

                logger.debug(f"Found {total} executions for workflow '{namespace}/{workflow_name}'")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing workflow executions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workflow executions: {str(e)}",
                )

        # ===========================================
        # Execution Authorization (task callbacks)
        # ===========================================

        @api.post("/executions/{exec_id}/authorize/{task_name}")
        async def execution_authorize_task(
            exec_id: str,
            task_name: str,
            request: Request,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Runtime task authorization callback — called by workers before each task.

            Not rate-limited: workers call this on every task execution. The endpoint
            requires a valid execution token bound to this specific exec_id, making
            brute-force impossible without a valid HMAC-signed token.
            """
            try:
                token_type = identity.metadata.get("token_type") if identity.metadata else None
                if token_type != "execution":
                    raise HTTPException(
                        status_code=403,
                        detail="This endpoint requires an execution token",
                    )

                token_exec_id = identity.metadata.get("exec_id")
                if token_exec_id != exec_id:
                    raise HTTPException(
                        status_code=403,
                        detail="Execution token is not bound to this execution",
                    )

                manager = ContextManager.create()
                try:
                    ctx = manager.get(exec_id)
                except Exception:
                    ctx = None
                if ctx is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{exec_id}' not found",
                    )

                from flux.domain import ExecutionState

                terminal_states = {
                    ExecutionState.COMPLETED,
                    ExecutionState.FAILED,
                    ExecutionState.CANCELLED,
                }
                if ctx.state in terminal_states:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Execution is not active (state: {ctx.state.value})",
                    )

                principal_subject = identity.metadata.get("principal_subject") or identity.subject
                principal_issuer = identity.metadata.get("principal_issuer", "flux")

                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(principal_subject, principal_issuer)
                if not principal:
                    raise HTTPException(status_code=403, detail="Principal not found")
                if not principal.enabled:
                    raise HTTPException(status_code=403, detail="Principal is disabled")

                workflow_meta = {}
                try:
                    _auth_ns = ctx.workflow_namespace
                    wf = WorkflowCatalog.create().get(_auth_ns, ctx.workflow_name)
                    workflow_meta = wf.metadata or {} if hasattr(wf, "metadata") else {}
                except Exception:
                    pass

                auth_exempt_tasks = set(workflow_meta.get("auth_exempt_tasks", []))
                if task_name in auth_exempt_tasks:
                    return {"authorized": True}

                if auth_service is not None:
                    roles = registry.get_roles(principal.id)
                    exec_identity = FluxIdentity(
                        subject=principal_subject,
                        roles=frozenset(roles),
                        metadata={"type": principal.type, "issuer": principal_issuer},
                    )
                    required = f"workflow:{_auth_ns}:{ctx.workflow_name}:task:{task_name}:execute"
                    authorized = await auth_service.is_authorized(exec_identity, required)
                    if not authorized:
                        raise HTTPException(
                            status_code=403,
                            detail={"authorized": False, "missing_permission": required},
                        )

                return {"authorized": True}

            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Execution authorize error for {exec_id}/{task_name}: {e}",
                )
                raise HTTPException(status_code=500, detail=str(e))
