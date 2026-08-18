"""Workflow catalog / run / lifecycle routes (`/workflows*`, `/namespaces`).

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
from fastapi import Header
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from sse_starlette import EventSourceResponse

from flux.catalogs import WorkflowCatalog
from flux.context_managers import ContextManager
from flux.errors import (
    ExecutionContextNotFoundError,
    HookNameConflictError,
    WorkerNotFoundError,
    WorkflowNotFoundError,
)
from flux.hooks.registry import HookRegistry
from flux.schedule_manager import create_schedule_manager
from flux.security.dependencies import get_identity
from flux.security.identity import ANONYMOUS, FluxIdentity
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.servers.models import redacted_response
from flux.utils import get_logger
from flux.utils import to_json
from flux.api.schemas import (
    MAX_WORKFLOW_UPLOAD_BYTES,
    WorkflowVersionResponse,
    routing_input_header_or_400,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class WorkflowRoutesMixin:
    def _register_workflow_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        @api.post("/workflows")
        async def workflows_save(
            file: UploadFile = File(...),
            identity: FluxIdentity = Depends(get_identity),
        ):
            chunk_size = 64 * 1024
            source_buffer = bytearray()
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                if len(source_buffer) + len(chunk) > MAX_WORKFLOW_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Workflow source too large: more than "
                            f"{MAX_WORKFLOW_UPLOAD_BYTES} bytes"
                        ),
                    )
                source_buffer.extend(chunk)
            source = bytes(source_buffer)
            logger.info(f"Received file: {file.filename} with size: {len(source)} bytes:")
            try:
                logger.debug(f"Processing workflow file: {file.filename}")
                catalog = WorkflowCatalog.create()
                # Static parse first — never execute uploaded code before the
                # caller is authorized to register in the target namespace(s).
                workflows = catalog.parse_static(source)

                if auth_service is not None and auth_config.enabled:
                    for wf in workflows:
                        required = f"workflow:{wf.namespace}:*:register"
                        if not await auth_service.is_authorized(identity, required):
                            raise HTTPException(
                                status_code=403,
                                detail=f"Permission denied: requires '{required}'",
                            )

                # Declaring hooks widens what registering this workflow can
                # do: each fires its target under a stored principal, so
                # workflow:*:register alone must not mint one -- the same
                # requires_code_upload_permission pattern agent definitions
                # use for tools_file/workflow_file/skills_dir. Checked before
                # enrich()/save() so an unauthorized declaration never
                # touches the database.
                for wf in workflows:
                    hook_specs = (wf.metadata or {}).get("hooks") or []
                    if not hook_specs:
                        continue
                    if auth_service is not None and auth_config.enabled:
                        required = "hook:*:create"
                        if not await auth_service.is_authorized(identity, required):
                            raise HTTPException(
                                status_code=403,
                                detail=f"Permission denied: requires '{required}'",
                            )
                    for spec in hook_specs:
                        await self._require_may_fire_as(
                            identity,
                            spec["principal"],
                            auth_config=auth_config,
                            auth_service=auth_service,
                            principal_registry=principal_registry,
                        )
                        await self._require_runnable_target(spec["principal"], spec["workflow"])

                # Authorized: now it is safe to import the module for metadata.
                catalog.enrich(source, workflows)

                result = catalog.save(workflows)
                logger.debug(f"Saved workflows: {[w.qualified_name for w in workflows]}")

                self._auto_create_schedules_from_source(source, workflows)

                # Every registered version reconciles the same owner-scoped
                # rows (hooks are not version-scoped, matching schedules) --
                # unconditionally, so a redeploy that drops the hooks= kwarg
                # removes previously-declared hooks instead of orphaning them.
                for wf in workflows:
                    HookRegistry.create().reconcile_owned_hooks(
                        owner_type="workflow",
                        owner_ref=f"{wf.namespace}/{wf.name}",
                        specs=(wf.metadata or {}).get("hooks") or [],
                        created_by=identity.subject if identity else None,
                    )

                return result
            except SyntaxError as e:
                logger.error(f"Syntax error while saving workflow: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except HookNameConflictError as e:
                # The workflow row above is already committed (catalog.save
                # happens before reconcile) -- this only keeps the response
                # honest about the hook half of the deployment failing,
                # rather than surfacing raw SQL via the generic handler below.
                logger.error(f"Hook name conflict while saving workflow: {str(e)}")
                raise HTTPException(status_code=409, detail=str(e))
            except Exception as e:
                logger.error(f"Error saving workflow: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error saving workflow: {str(e)}")

        @api.get("/namespaces")
        async def list_namespaces(
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                catalog = WorkflowCatalog.create()
                visible: dict[str, int] = {}
                if auth_service is not None and auth_config.enabled:
                    permissions = await auth_service.resolve_permissions(identity)
                    for wf in catalog.all():
                        required = f"workflow:{wf.namespace}:{wf.name}:read"
                        if identity.has_permission(required, permissions):
                            visible[wf.namespace] = visible.get(wf.namespace, 0) + 1
                else:
                    for wf in catalog.all():
                        visible[wf.namespace] = visible.get(wf.namespace, 0) + 1
                return [{"namespace": ns, "workflow_count": n} for ns, n in sorted(visible.items())]
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing namespaces: {str(e)}",
                )

        @api.get("/workflows")
        async def workflows_all(
            namespace: str | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug("Fetching all workflows")
                catalog = WorkflowCatalog.create()
                workflows = catalog.all(namespace=namespace)
                if auth_service is not None and auth_config.enabled:
                    permissions = await auth_service.resolve_permissions(identity)
                    filtered = []
                    for w in workflows:
                        required = f"workflow:{w.namespace}:{w.name}:read"
                        if identity.has_permission(required, permissions):
                            filtered.append(w)
                    workflows = filtered
                result = [
                    {"namespace": w.namespace, "name": w.name, "version": w.version}
                    for w in workflows
                ]
                logger.debug(f"Found {len(result)} workflows")
                return result
            except Exception as e:
                logger.error(f"Error listing workflows: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing workflows: {str(e)}")

        @api.get("/workflows/{namespace}/{workflow_name}")
        async def workflows_get_ns(
            namespace: str,
            workflow_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )
                logger.debug(f"Fetching workflow: {namespace}/{workflow_name}")
                catalog = WorkflowCatalog.create()
                workflow = catalog.get(namespace, workflow_name)
                logger.debug(
                    f"Found workflow: {namespace}/{workflow_name} (version: {workflow.version})",
                )
                return workflow.to_dict()
            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error retrieving workflow: {str(e)}")

        @api.post("/workflows/{namespace}/{workflow_name}/run/{mode}")
        async def workflows_run_ns(
            namespace: str,
            workflow_name: str,
            input: Any = Body(None),
            mode: str = "async",
            detailed: bool = False,
            version: int | None = None,
            park_ttl: int | None = None,
            name: str | None = None,
            preferred_worker: str | None = Header(None, alias="X-Flux-Preferred-Worker"),
            required_worker: str | None = Header(None, alias="X-Flux-Require-Worker"),
            # list[str] so FastAPI uses getlist(): as `str` it would silently
            # keep only the first of repeated headers (#211).
            routing_input: list[str] | None = Header(None, alias="X-Flux-Routing-Input"),
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Running workflow: {namespace}/{workflow_name} (version: {version or 'latest'}) "
                    f"| Mode: {mode} | Detailed: {detailed}",
                )
                logger.debug(f"Input: {to_json(input)}")

                if not workflow_name:
                    raise HTTPException(status_code=400, detail="Workflow name is required.")

                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                if auth_service is not None and auth_config.enabled:
                    workflow_info = WorkflowCatalog.create().get(namespace, workflow_name, version)
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        workflow_name,
                        workflow_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                # The sticky-routing hint is caller-supplied header input:
                # bound and sanitize before it reaches the database. Invalid
                # values are dropped, not rejected — it is only a hint.
                if preferred_worker is not None:
                    preferred_worker = preferred_worker.strip()
                    if not preferred_worker or len(preferred_worker) > 256:
                        preferred_worker = None

                # The binding header takes the opposite policy (issue #187):
                # dropping a malformed value would dispatch the execution
                # anywhere, which is indistinguishable from having honoured it.
                if required_worker is not None:
                    required_worker = required_worker.strip()
                    if not required_worker or len(required_worker) > 256:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "X-Flux-Require-Worker must be a non-empty worker name "
                                "of at most 256 characters"
                            ),
                        )
                    # Pinning work to a named node concentrates load there and
                    # compels that node to run the code, so it needs a
                    # worker-scoped grant on top of the run permission.
                    if auth_service is not None and auth_config.enabled:
                        needed = f"worker:{required_worker}:target"
                        if not await auth_service.is_authorized(identity, needed):
                            raise HTTPException(
                                status_code=403,
                                detail=f"Permission denied: requires '{needed}'",
                            )
                    # Registered, not necessarily online: binding to a worker
                    # that is currently down is legitimate (it parks until the
                    # worker returns), but a name no worker ever had can only
                    # park forever, and park_ttl defaults to no bound.
                    from flux.worker_registry import WorkerRegistry

                    try:
                        WorkerRegistry.create().get(required_worker)
                    except WorkerNotFoundError:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"X-Flux-Require-Worker names an unknown worker "
                                f"'{required_worker}'; it has never registered"
                            ),
                        )
                    # A binding leaves one eligible worker, so the hint has
                    # nothing to order. Dropped rather than rejected because
                    # FluxClient.for_current_execution() injects it as a
                    # client-level default.
                    preferred_worker = None

                # Per-run park-TTL override (issue #157): bounds how long this
                # execution may wait unclaimed. None defers to the server
                # default; 0 explicitly parks forever (batch callers).
                if park_ttl is not None and park_ttl < 0:
                    raise HTTPException(
                        status_code=400,
                        detail="park_ttl must be >= 0 (0 disables the bound)",
                    )

                # Operator-facing label (agent console session titles):
                # stripped and bounded here, same as the rename route.
                if name is not None:
                    name = name.strip()
                    if not name or len(name) > 200:
                        raise HTTPException(
                            status_code=400,
                            detail="name must be non-empty and at most 200 characters",
                        )

                # Routing-only values (issue #211): matched at dispatch, never
                # delivered.
                parsed_routing_input = routing_input_header_or_400(routing_input)

                ctx = self._create_execution(
                    namespace,
                    workflow_name,
                    input,
                    version,
                    preferred_worker=preferred_worker,
                    required_worker=required_worker,
                    routing_input=parsed_routing_input,
                    park_ttl=park_ttl,
                    name=name,
                )

                if parsed_routing_input:
                    # Key names only. They are already public (they appear in
                    # the workflow source); the values and their presence are
                    # not, and this log is not reachable through the execution
                    # API a worker can call. It is the only operator-visible
                    # trace of a routing directive, by design.
                    logger.info(
                        f"Execution {ctx.execution_id} carries routing input "
                        f"keys: {sorted(parsed_routing_input)}",
                    )
                manager = ContextManager.create()

                # Record agent-session linkage for "agents" namespace runs so
                # fleet queries don't have to crack open pickled execution
                # inputs. Best-effort: a failure here must not block the run.
                if namespace == "agents" and isinstance(input, dict):
                    agent_field = input.get("agent")
                    if isinstance(agent_field, str) and agent_field:
                        session_db = self._get_db_session()
                        try:
                            from flux.models import AgentSessionModel

                            session_db.add(
                                AgentSessionModel(
                                    execution_id=ctx.execution_id,
                                    agent_name=agent_field,
                                ),
                            )
                            session_db.commit()
                        except Exception:
                            session_db.rollback()
                            logger.warning(
                                "Failed to record agent_session for %s",
                                ctx.execution_id,
                                exc_info=True,
                            )
                        finally:
                            session_db.close()

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
                        from flux.models import ExecutionContextModel as _ECM3

                        exec_row = token_session.get(_ECM3, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            token_session.commit()
                    finally:
                        token_session.close()
                logger.debug(
                    f"Created execution context: {ctx.execution_id} for workflow: {namespace}/{workflow_name}",
                )

                # Register execution event BEFORE notifying workers to avoid
                # race where worker checkpoints before event exists.
                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(
                        ctx.execution_id,
                        asyncio.Event(),
                    )

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

                response = await redacted_response(ctx, detailed=detailed)
                logger.debug(
                    f"Returning execution result for {ctx.execution_id} in state: {ctx.state.value}",
                )
                return response

            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error scheduling workflow {namespace}/{workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.post("/workflows/{namespace}/{workflow_name}/resume/{execution_id}/{mode}")
        async def workflows_resume_ns(
            namespace: str,
            workflow_name: str,
            execution_id: str,
            input: Any = Body(None),
            mode: str = "async",
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Resuming workflow: {namespace}/{workflow_name} | Execution ID: {execution_id} | Mode: {mode} | Detailed: {detailed}",
                )
                logger.debug(f"Input: {to_json(input)}")

                if not execution_id:
                    raise HTTPException(status_code=400, detail="Execution ID is required.")

                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                manager = ContextManager.create()

                try:
                    ctx = manager.get(execution_id)
                except ExecutionContextNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution context with ID {execution_id} not found.",
                    )

                if ctx.workflow_namespace != namespace or ctx.workflow_name != workflow_name:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Execution {execution_id} does not belong to "
                            f"workflow {namespace}/{workflow_name}."
                        ),
                    )

                if auth_service is not None and auth_config.enabled:
                    workflow_info = WorkflowCatalog.create().get(
                        ctx.workflow_namespace,
                        ctx.workflow_name,
                    )
                    result = await auth_service.authorize(
                        identity,
                        ctx.workflow_namespace,
                        ctx.workflow_name,
                        workflow_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
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
                        from flux.models import ExecutionContextModel as _ECM4

                        exec_row = resume_token_session.get(_ECM4, ctx.execution_id)
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

                logger.debug(
                    f"Resuming execution context: {ctx.execution_id} for workflow: {namespace}/{workflow_name}",
                )

                # Register execution event BEFORE notifying workers to avoid
                # race where worker checkpoints before event exists.
                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(
                        ctx.execution_id,
                        asyncio.Event(),
                    )

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

                response = await redacted_response(ctx, detailed=detailed)
                logger.debug(
                    f"Returning execution result for {ctx.execution_id} in state: {ctx.state.value}",
                )
                return response

            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error scheduling workflow {namespace}/{workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.get("/workflows/{namespace}/{workflow_name}/status/{execution_id}")
        async def workflows_status_ns(
            namespace: str,
            workflow_name: str,
            execution_id: str,
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Checking status for workflow: {namespace}/{workflow_name} | Execution ID: {execution_id}",
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

                manager = ContextManager.create()
                context = manager.get(execution_id)
                # The permission check above covers the *URL's* workflow only;
                # without this ownership check a caller could read another
                # workflow's execution state by passing its execution ID here.
                if (
                    context.workflow_namespace != namespace
                    or context.workflow_name != workflow_name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Execution {execution_id} does not belong to "
                            f"workflow {namespace}/{workflow_name}."
                        ),
                    )
                result = await redacted_response(context, detailed=detailed)
                logger.debug(f"Status for {execution_id}: {context.state.value}")
                return result
            except ExecutionContextNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Execution context with ID {execution_id} not found.",
                )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error inspecting workflow: {str(e)}")

        @api.get("/workflows/{namespace}/{workflow_name}/cancel/{execution_id}")
        async def workflows_cancel_ns(
            namespace: str,
            workflow_name: str,
            execution_id: str,
            mode: str = "async",
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Cancelling workflow: {namespace}/{workflow_name} | Execution ID: {execution_id} | Mode: {mode}",
                )

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:run",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:run'",
                        )

                if not execution_id:
                    raise HTTPException(status_code=400, detail="Execution ID is required.")

                if mode and mode not in ["sync", "async"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async'.",
                    )

                manager = ContextManager.create()
                try:
                    ctx = manager.get(execution_id)
                except ExecutionContextNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution context with ID {execution_id} not found.",
                    )

                # Authorization above is scoped to the URL's workflow; verify the
                # execution actually belongs to it so an execution ID can't be
                # used to cancel a different workflow's run.
                if ctx.workflow_namespace != namespace or ctx.workflow_name != workflow_name:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Execution {execution_id} does not belong to "
                            f"workflow {namespace}/{workflow_name}."
                        ),
                    )

                if ctx.has_finished:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot cancel a finished execution.",
                    )

                from flux.unit_of_work import UnitOfWork
                from flux.approvals import ApprovalManager

                with UnitOfWork() as uow:
                    ApprovalManager().cancel_pending_for_execution(execution_id, uow=uow)
                    ctx.start_cancel()
                    manager.save(ctx, uow=uow)
                    uow.commit()

                self._execution_queue_times.pop(execution_id, None)

                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_workflow_completed(
                        ctx.workflow_namespace,
                        workflow_name,
                        "cancel_requested",
                        0,
                    )

                # Register execution event BEFORE notifying workers to avoid
                # race where worker checkpoints before event exists.
                if mode == "sync":
                    self._execution_events.setdefault(
                        ctx.execution_id,
                        asyncio.Event(),
                    )

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            logger.debug(
                                f"Waiting for cancellation of {execution_id}, current state: {ctx.state.value}",
                            )
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                dto = ExecutionContextDTO.from_domain(ctx)
                result = await redacted_response(ctx, detailed=detailed)
                logger.info(
                    f"Workflow {namespace}/{workflow_name} execution {execution_id} is {dto.state}.",
                )
                return result
            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except WorkerNotFoundError as e:
                logger.error(f"Worker not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException as he:
                logger.error(
                    f"HTTP error while cancelling workflow {namespace}/{workflow_name}: {str(he)}",
                )
                raise
            except Exception as e:
                logger.error(f"Error cancelling workflow {namespace}/{workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error cancelling workflow: {str(e)}")

        # ===========================================
        # Workflow Version Management Endpoints
        # ===========================================

        @api.delete("/workflows/{namespace}/{workflow_name}")
        async def workflow_delete_ns(
            namespace: str,
            workflow_name: str,
            version: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Delete workflow by namespace/name, optionally specific version."""
            if auth_service is not None and auth_config.enabled:
                required = f"workflow:{namespace}:*:register"
                if not await auth_service.is_authorized(identity, required):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Permission denied: requires '{required}'",
                    )
            try:
                logger.info(
                    f"Deleting workflow '{namespace}/{workflow_name}'"
                    + (f" version {version}" if version else " (all versions)"),
                )

                catalog = WorkflowCatalog.create()

                try:
                    catalog.get(namespace, workflow_name, version)
                except WorkflowNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{namespace}/{workflow_name}'"
                        + (f" version {version}" if version else "")
                        + " not found",
                    )

                # Schedules hold a real FK to workflows.id, which is
                # version-scoped -- so deleting a workflow row a schedule
                # still points at does not orphan the row, it fails the
                # delete outright (SQLAlchemy nullifies a NOT NULL FK).
                # The schedules bound to the versions being removed are
                # therefore resolved first, through the FK itself rather
                # than the denormalized ref columns; auto-created or
                # operator-created makes no difference, the binding is what
                # matters. Where they go depends on whether the workflow
                # survives: firing resolves it by ref, so a schedule whose
                # bound version is deleted out from under a still-registered
                # workflow is re-pointed at the newest surviving version
                # rather than silently stopping.
                all_versions = catalog.versions(namespace, workflow_name)
                removed_ids = {
                    v.id for v in all_versions if version is None or v.version == version
                }
                surviving = [v for v in all_versions if v.id not in removed_ids]
                schedule_manager = create_schedule_manager()
                bound_schedules = [
                    bound
                    for workflow_id in removed_ids
                    for bound in schedule_manager.list_schedules(
                        workflow_id=workflow_id,
                        active_only=False,
                    )
                ]

                if bound_schedules and surviving:
                    # versions() orders newest first.
                    heir = surviving[0]
                    taken = {
                        s.name
                        for s in schedule_manager.list_schedules(
                            workflow_id=heir.id,
                            active_only=False,
                        )
                    }
                    for bound_schedule in bound_schedules:
                        # (workflow_id, name) is unique: a duplicate left by
                        # the pre-reconciliation lookup cannot be re-pointed
                        # onto a name the heir already carries, and does not
                        # need to be -- that row is the one that survives.
                        if bound_schedule.name in taken:
                            schedule_manager.delete_schedule(bound_schedule.id)
                            continue
                        schedule_manager.update_schedule(
                            schedule_id=bound_schedule.id,
                            workflow_id=heir.id,
                        )
                        taken.add(bound_schedule.name)
                    logger.info(
                        f"Re-pointed {len(bound_schedules)} schedule(s) of workflow "
                        f"'{namespace}/{workflow_name}' at version {heir.version}",
                    )
                elif bound_schedules:
                    for bound_schedule in bound_schedules:
                        schedule_manager.delete_schedule(bound_schedule.id)
                    logger.info(
                        f"Deleted {len(bound_schedules)} schedule(s) bound to workflow "
                        f"'{namespace}/{workflow_name}'",
                    )

                catalog.delete(namespace, workflow_name, version)

                # A specific-version delete only removes the workflow
                # entirely when it was the last version left; hooks are
                # keyed on the workflow's identity, not a version, so
                # cleanup mirrors that -- deleting one of several versions
                # must not touch hooks still owned by the surviving ones.
                still_exists = True
                try:
                    catalog.get(namespace, workflow_name)
                except WorkflowNotFoundError:
                    still_exists = False

                if not still_exists:
                    removed = HookRegistry.create().delete_owned_hooks(
                        owner_type="workflow",
                        owner_ref=f"{namespace}/{workflow_name}",
                    )
                    if removed:
                        logger.info(
                            f"Deleted {removed} hook(s) owned by workflow "
                            f"'{namespace}/{workflow_name}'",
                        )

                logger.info(f"Successfully deleted workflow '{namespace}/{workflow_name}'")
                return {
                    "status": "success",
                    "message": f"Workflow '{namespace}/{workflow_name}'"
                    + (f" version {version}" if version else " (all versions)")
                    + " deleted successfully",
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error deleting workflow: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error deleting workflow: {str(e)}",
                )

        @api.get(
            "/workflows/{namespace}/{workflow_name}/versions",
            response_model=list[WorkflowVersionResponse],
        )
        async def workflow_versions_ns(
            namespace: str,
            workflow_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List all versions of a workflow."""
            try:
                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )
                logger.debug(f"Fetching versions for workflow: {namespace}/{workflow_name}")

                catalog = WorkflowCatalog.create()
                versions = catalog.versions(namespace, workflow_name)

                if not versions:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{namespace}/{workflow_name}' not found",
                    )

                result = [
                    WorkflowVersionResponse(
                        id=v.id,
                        name=v.name,
                        version=v.version,
                    )
                    for v in versions
                ]
                logger.debug(
                    f"Found {len(result)} versions for workflow '{namespace}/{workflow_name}'",
                )
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing workflow versions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workflow versions: {str(e)}",
                )

        @api.get("/workflows/{namespace}/{workflow_name}/versions/{version}")
        async def workflow_version_get_ns(
            namespace: str,
            workflow_name: str,
            version: int,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get specific workflow version."""
            try:
                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )
                logger.debug(f"Fetching workflow '{namespace}/{workflow_name}' version {version}")

                catalog = WorkflowCatalog.create()
                workflow = catalog.get(namespace, workflow_name, version)

                logger.debug(f"Found workflow '{namespace}/{workflow_name}' version {version}")
                return workflow.to_dict()

            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                logger.error(f"Error retrieving workflow version: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving workflow version: {str(e)}",
                )
