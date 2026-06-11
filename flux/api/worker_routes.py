"""Worker registration / dispatch / reporting routes (`/workers*`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio
import base64
import hmac
import time
from typing import Literal
from uuid import uuid4

from fastapi import Body
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from sse_starlette import EventSourceResponse

from flux.catalogs import WorkflowCatalog
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionContextNotFoundError, WorkerNotFoundError
from flux.secret_managers import SecretManager
from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import FluxIdentity
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.utils import get_logger
from flux.utils import to_json
from flux.worker_registry import WorkerInfo
from flux.worker_registry import WorkerRegistry
from flux.api.schemas import (
    _inject_trace_context,
    WorkerRuntimeModel,
    WorkerGPUModel,
    WorkerResourcesModel,
    WorkerRegistration,
    WorkerResponse,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class WorkerRoutesMixin:
    def _register_worker_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        @api.post("/workers/register")
        async def workers_register(
            registration: WorkerRegistration = Body(...),
            authorization: str = Header(None),
        ):
            try:
                logger.debug(f"Worker registration request: {registration.name}")
                token = self._extract_token(authorization)
                expected = self._bootstrap_token
                if not expected or not token or not hmac.compare_digest(expected, token):
                    logger.warning(f"Invalid bootstrap token for worker: {registration.name}")
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid bootstrap token.",
                    )

                registry = WorkerRegistry.create()
                result = registry.register(
                    registration.name,
                    registration.runtime,
                    registration.packages,
                    registration.resources,
                    labels=registration.labels,
                )

                if auth_service is not None and auth_config.api_keys.enabled:
                    principal = principal_registry.find(
                        subject=registration.name,
                        external_issuer="flux",
                    )
                    if not principal:
                        principal = principal_registry.create(
                            type="service_account",
                            subject=registration.name,
                            external_issuer="flux",
                        )
                    if not principal.enabled:
                        principal_registry.set_enabled(principal.id, True)
                    existing_roles = principal_registry.get_roles(principal.id)
                    if "worker" not in existing_roles:
                        principal_registry.assign_role(principal.id, "worker")
                    await auth_service.revoke_all_api_keys(principal.id)
                    api_key = await auth_service.create_api_key(
                        principal.id,
                        key_name=f"worker-{registration.name}",
                    )
                    result.session_token = api_key

                    from flux.observability import get_metrics as _gm_prov

                    _m_prov = _gm_prov()
                    if _m_prov:
                        _m_prov.record_worker_auth_event(
                            registration.name,
                            "principal_provisioned",
                        )

                self._worker_cache[registration.name] = WorkerResponse(
                    name=registration.name,
                    status="online" if registration.name in self._worker_names else "offline",
                    runtime=WorkerRuntimeModel(
                        os_name=registration.runtime.os_name,
                        os_version=registration.runtime.os_version,
                        python_version=registration.runtime.python_version,
                    ),
                    resources=WorkerResourcesModel(
                        cpu_total=registration.resources.cpu_total,
                        cpu_available=registration.resources.cpu_available,
                        memory_total=registration.resources.memory_total,
                        memory_available=registration.resources.memory_available,
                        disk_total=registration.resources.disk_total,
                        disk_free=registration.resources.disk_free,
                        gpus=[
                            WorkerGPUModel(
                                name=g.name,
                                memory_total=g.memory_total,
                                memory_available=g.memory_available,
                            )
                            for g in registration.resources.gpus
                        ]
                        if registration.resources.gpus
                        else [],
                    ),
                    packages=[
                        {"name": p["name"], "version": p["version"]} for p in registration.packages
                    ]
                    if registration.packages
                    else [],
                    labels=registration.labels,
                )
                if registration.name in self._worker_names:
                    self._worker_offline_since.pop(registration.name, None)
                else:
                    self._worker_offline_since[registration.name] = time.monotonic()

                logger.info(f"Worker registered successfully: {registration.name}")
                logger.debug(
                    f"Worker details: OS: {registration.runtime.os_name} {registration.runtime.os_version}, "
                    f"Python: {registration.runtime.python_version}, "
                    f"Resources: CPU: {registration.resources.cpu_total}, "
                    f"Memory: {registration.resources.memory_total}",
                )

                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_worker_registered(registration.name)

                return result
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Worker registration failed for {registration.name}: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=400,
                    detail=str(e),
                )

        @api.post("/workers/{name}/pong")
        async def workers_pong(
            name: str,
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            """Receive heartbeat pong from a worker."""
            try:
                self._verify_worker_identity(identity, name)
                self._worker_last_pong[name] = time.monotonic()
                self._worker_stale_since.pop(name, None)
                logger.debug(f"Pong received from worker {name}")
                return {"status": "ok"}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/workers/{name}/connect")
        async def workers_connect(
            name: str,
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            try:
                logger.debug(f"Worker connection request: {name}")
                self._verify_worker_identity(identity, name)
                registry = WorkerRegistry.create()
                worker = registry.get(name)
                logger.info(f"Worker connected: {name}")

                worker_event = asyncio.Event()
                eviction_event = asyncio.Event()
                self._worker_events[name] = worker_event
                self._worker_evicted[name] = eviction_event
                if name not in self._worker_names:
                    self._worker_names.append(name)
                self._worker_last_pong[name] = time.monotonic()
                self._worker_stale_since.pop(name, None)
                self._worker_offline_since.pop(name, None)
                gen = self._worker_connection_gen.get(name, 0) + 1
                self._worker_connection_gen[name] = gen
                if name in self._worker_cache:
                    self._worker_cache[name].status = "online"

                from flux.observability import get_metrics as _get_metrics

                _m = _get_metrics()
                if _m:
                    _m.record_worker_connected(name)

                logger.debug(
                    f"Worker {name} registered for round-robin (total: {len(self._worker_names)})",
                )

                async def check_for_new_executions():
                    fallback_interval = 0.5
                    last_ping_time = time.monotonic()

                    context_manager = ContextManager.create()
                    try:
                        while True:
                            try:
                                if eviction_event.is_set():
                                    logger.info(f"Worker {name} evicted by reaper, closing SSE")
                                    return

                                now = time.monotonic()
                                if (now - last_ping_time) >= self.heartbeat_interval:
                                    last_ping_time = now
                                    yield {
                                        "event": "ping",
                                        "data": "",
                                    }

                                ctx = context_manager.next_execution(worker)
                                if ctx:
                                    _exec_ns = ctx.workflow_namespace
                                    workflow = WorkflowCatalog.create().get(
                                        _exec_ns,
                                        ctx.workflow_name,
                                    )
                                    workflow.source = base64.b64encode(workflow.source).decode(
                                        "utf-8",
                                    )

                                    logger.debug(
                                        f"Sending execution to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    payload_dict = {"workflow": workflow, "context": ctx}
                                    exec_model_session = self._get_db_session()
                                    try:
                                        from flux.models import ExecutionContextModel as _ECM

                                        exec_row = exec_model_session.get(
                                            _ECM,
                                            ctx.execution_id,
                                        )
                                        exec_token_for_dispatch = (
                                            exec_row.exec_token if exec_row else None
                                        )
                                    finally:
                                        exec_model_session.close()
                                    if exec_token_for_dispatch:
                                        payload_dict["exec_token"] = exec_token_for_dispatch
                                    data_payload = to_json(payload_dict)

                                    data_payload = _inject_trace_context(data_payload)

                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_scheduled",
                                        "data": data_payload,
                                    }
                                    logger.debug(
                                        f"Execution {ctx.execution_id} scheduled for worker {name}",
                                    )
                                    continue

                                ctx = context_manager.next_cancellation(worker)
                                if ctx:
                                    logger.debug(
                                        f"Sending cancellation to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_cancelled",
                                        "data": _inject_trace_context(
                                            to_json({"context": ctx}),
                                        ),
                                    }

                                    logger.debug(
                                        f"Cancellation {ctx.execution_id} sent to worker {name}",
                                    )
                                    continue

                                ctx = context_manager.next_resume(worker)
                                if ctx:
                                    _resume_ns = ctx.workflow_namespace
                                    workflow = WorkflowCatalog.create().get(
                                        _resume_ns,
                                        ctx.workflow_name,
                                    )
                                    workflow.source = base64.b64encode(workflow.source).decode(
                                        "utf-8",
                                    )
                                    logger.debug(
                                        f"Sending resume to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    resume_payload_dict = {"workflow": workflow, "context": ctx}
                                    resume_model_session = self._get_db_session()
                                    try:
                                        from flux.models import ExecutionContextModel as _ECM2

                                        resume_exec_row = resume_model_session.get(
                                            _ECM2,
                                            ctx.execution_id,
                                        )
                                        resume_exec_token = (
                                            resume_exec_row.exec_token if resume_exec_row else None
                                        )
                                    finally:
                                        resume_model_session.close()
                                    if resume_exec_token:
                                        resume_payload_dict["exec_token"] = resume_exec_token
                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_resumed",
                                        "data": _inject_trace_context(
                                            to_json(resume_payload_dict),
                                        ),
                                    }

                                    logger.debug(
                                        f"Resumption {ctx.execution_id} sent to worker {name}",
                                    )
                                    continue

                                # No work found — wait for per-worker signal or fallback
                                worker_event.clear()
                                self._work_available.clear()
                                try:
                                    worker_task = asyncio.ensure_future(worker_event.wait())
                                    broadcast_task = asyncio.ensure_future(
                                        self._work_available.wait(),
                                    )
                                    done, pending = await asyncio.wait(
                                        [worker_task, broadcast_task],
                                        timeout=fallback_interval,
                                        return_when=asyncio.FIRST_COMPLETED,
                                    )
                                    for task in pending:
                                        task.cancel()
                                        try:
                                            await task
                                        except asyncio.CancelledError:
                                            pass
                                except Exception:
                                    pass
                            except Exception as e:
                                logger.error(
                                    f"Error in worker connection stream for {name}: {str(e)}",
                                )
                                yield {
                                    "event": "error",
                                    "data": str(e),
                                }
                    finally:
                        if self._worker_connection_gen.get(name) == gen:
                            self._disconnect_worker(name)
                            logger.info(
                                f"Worker {name} disconnected (remaining: {len(self._worker_names)})",
                            )
                        else:
                            logger.debug(
                                f"Stale SSE for {name} closed (superseded by newer connection)",
                            )

                return EventSourceResponse(
                    check_for_new_executions(),
                    media_type="text/event-stream",
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    },
                )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=404, detail=str(e))

        @api.post("/workers/{name}/claim/{execution_id}")
        async def workers_claim(
            name: str,
            execution_id: str,
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            from flux.domain import ExecutionState

            try:
                logger.debug(f"Worker {name} claiming execution: {execution_id}")
                self._verify_worker_identity(identity, name)
                registry = WorkerRegistry.create()
                worker = registry.get(name)
                context_manager = ContextManager.create()

                try:
                    current = context_manager.get(execution_id)
                except ExecutionContextNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution {execution_id} not found.",
                    )

                from flux.errors import ExecutionError

                if current.state in (ExecutionState.CREATED, ExecutionState.SCHEDULED):
                    ctx = context_manager.claim(execution_id, worker)
                    is_resume_claim = False
                elif current.state == ExecutionState.RESUME_SCHEDULED:
                    try:
                        ctx = context_manager.claim_resume(execution_id, worker)
                    except ExecutionError as e:
                        raise HTTPException(status_code=409, detail=str(e))
                    is_resume_claim = True
                else:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Cannot claim execution {execution_id}: "
                            f"current state is {current.state.value}"
                        ),
                    )

                logger.info(f"Execution {execution_id} claimed by worker {name}")

                from flux.observability import get_metrics

                m = get_metrics()
                if m and not is_resume_claim:
                    queued_at = self._execution_queue_times.pop(execution_id, None)
                    schedule_to_start = time.monotonic() - queued_at if queued_at else None
                    m.record_execution_claimed(schedule_to_start)

                # Notify any waiting sync/stream endpoint
                event = self._execution_events.get(execution_id)
                if event:
                    event.set()

                return ctx.to_dict()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error claiming execution {execution_id} by worker {name}: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/workers/{name}/checkpoint/{execution_id}")
        async def workers_checkpoint(
            name: str,
            execution_id: str,
            context: ExecutionContextDTO = Body(...),
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            try:
                logger.debug(
                    f"Checkpoint request from worker: {name} for execution: {execution_id}",
                )
                logger.debug(f"Execution state: {context.state}")

                self._verify_worker_identity(identity, name)
                self._worker_last_pong[name] = time.monotonic()

                context_manager = ContextManager.create()
                domain_ctx = context.to_domain()

                try:
                    ctx = context_manager.update(domain_ctx)
                except ExecutionContextNotFoundError:
                    logger.warning(f"Execution context not found: {execution_id}")
                    raise HTTPException(status_code=404, detail="Execution context not found.")
                logger.debug(f"Checkpoint saved for {execution_id}, state: {ctx.state.value}")

                if ctx.has_finished:
                    self._execution_queue_times.pop(execution_id, None)

                # Notify any waiting sync/stream endpoint
                event = self._execution_events.get(execution_id)
                if event:
                    event.set()

                return ctx.summary()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error checkpointing execution: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))

        @api.post("/workers/{name}/progress/{execution_id}")
        async def workers_progress(
            name: str,
            execution_id: str,
            events: list = Body(...),
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            self._verify_worker_identity(identity, name)
            self._worker_last_pong[name] = time.monotonic()

            buffer = self._progress_buffers.get(execution_id)
            if not buffer:
                return {"status": "ok"}

            for event in events:
                progress_event = ExecutionEvent(
                    type=ExecutionEventType.TASK_PROGRESS,
                    source_id=event.get("task_id", ""),
                    name=event.get("task_name", ""),
                    value=event.get("value"),
                )
                try:
                    buffer.put_nowait(progress_event)
                except asyncio.QueueFull:
                    pass
            return {"status": "ok"}

        @api.post("/workers/{name}/secrets/batch")
        async def workers_secrets_batch(
            name: str,
            payload: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            self._verify_worker_identity(identity, name)
            execution_id = payload.get("execution_id")
            keys = payload.get("names") or []
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise HTTPException(status_code=400, detail="'names' must be a list of strings")
            if not execution_id:
                raise HTTPException(status_code=400, detail="'execution_id' is required")

            ctx = ContextManager.create().get(execution_id)
            if ctx is None:
                raise HTTPException(status_code=404, detail="Execution not found")
            if ctx.current_worker != name:
                raise HTTPException(
                    status_code=403,
                    detail="Worker does not own this execution",
                )

            try:
                wf = WorkflowCatalog.create().get(ctx.workflow_namespace, ctx.workflow_name)
            except Exception:
                raise HTTPException(status_code=404, detail="Workflow not found")
            declared = set((getattr(wf, "metadata", None) or {}).get("secret_requests", []) or [])

            requested = set(keys)
            disallowed = requested - declared
            if disallowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Secrets not declared by workflow: {sorted(disallowed)}",
                )

            try:
                return await SecretManager.current().get(list(requested))
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        # ===========================================
        # Worker Management Endpoints
        # ===========================================

        def _worker_info_to_response(w: WorkerInfo) -> WorkerResponse:
            """Convert WorkerInfo to WorkerResponse."""
            status = "online" if w.name in self._worker_names else "offline"
            worker_response = WorkerResponse(
                name=w.name,
                status=status,
                packages=[{"name": p["name"], "version": p["version"]} for p in w.packages]
                if w.packages
                else [],
                labels=w.labels if isinstance(w.labels, dict) else {},
            )

            if w.runtime:
                worker_response.runtime = WorkerRuntimeModel(
                    os_name=w.runtime.os_name,
                    os_version=w.runtime.os_version,
                    python_version=w.runtime.python_version,
                )

            if w.resources:
                worker_response.resources = WorkerResourcesModel(
                    cpu_total=w.resources.cpu_total,
                    cpu_available=w.resources.cpu_available,
                    memory_total=w.resources.memory_total,
                    memory_available=w.resources.memory_available,
                    disk_total=w.resources.disk_total,
                    disk_free=w.resources.disk_free,
                    gpus=[
                        WorkerGPUModel(
                            name=g.name,
                            memory_total=g.memory_total,
                            memory_available=g.memory_available,
                        )
                        for g in w.resources.gpus
                    ]
                    if w.resources.gpus
                    else [],
                )

            return worker_response

        @api.get("/workers", response_model=list[WorkerResponse])
        async def workers_list(
            status: Literal["online", "offline"] | None = Query(None),
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List workers from in-memory cache. Optional ?status=online|offline filter.

            Worker visibility is intentionally unpermissioned — any authenticated user
            may discover available workers. Sensitive details are not exposed.
            """
            try:
                logger.debug(f"Listing workers (filter={status})")

                if status == "online":
                    result = [
                        self._worker_cache[n] for n in self._worker_names if n in self._worker_cache
                    ]
                elif status == "offline":
                    result = [
                        self._worker_cache[n]
                        for n in self._worker_offline_since
                        if n in self._worker_cache
                    ]
                else:
                    result = list(self._worker_cache.values())

                logger.debug(f"Found {len(result)} workers")
                return result

            except Exception as e:
                logger.error(f"Error listing workers: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workers: {str(e)}",
                )

        @api.get("/workers/{name}", response_model=WorkerResponse)
        async def worker_get(
            name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get worker details, from cache first, DB fallback."""
            try:
                logger.debug(f"Fetching worker: {name}")

                if name in self._worker_cache:
                    return self._worker_cache[name]

                # Fallback to DB for historical workers
                registry = WorkerRegistry.create()
                w = registry.get(name)

                logger.debug(f"Found worker: {name}")
                return _worker_info_to_response(w)

            except WorkerNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Worker '{name}' not found",
                )
            except Exception as e:
                logger.error(f"Error retrieving worker: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving worker: {str(e)}",
                )
