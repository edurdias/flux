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
from fastapi import Request
from fastapi import Response
from sse_starlette import EventSourceResponse

from flux.config import Configuration

from flux.catalogs import WorkflowCatalog
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionContextNotFoundError, StaleClaimError, WorkerNotFoundError
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
    def _build_dispatch_payload(self: Server, ctx) -> dict:  # type: ignore[misc]
        """Assemble the SSE payload for dispatching/resuming an execution.

        Performs the workflow-source load and exec-token lookup — both blocking
        DB reads — so callers invoke it via ``asyncio.to_thread`` to keep the
        worker SSE stream and the event loop unblocked.
        """
        from flux.models import ExecutionContextModel

        workflow = WorkflowCatalog.create().get(ctx.workflow_namespace, ctx.workflow_name)
        # source travels base64-encoded on the wire; the worker decodes it.
        workflow.source = base64.b64encode(workflow.source).decode("utf-8")  # type: ignore[assignment]
        payload: dict = {"workflow": workflow, "context": ctx}
        if (workflow.metadata or {}).get("durability") == "transient":
            # The worker suppresses intermediate (task-level) checkpoints for
            # transient workflows; only the terminal workflow state persists.
            payload["transient"] = True
        required_runner = (workflow.metadata or {}).get("runner")
        if required_runner:
            payload["runner"] = required_runner
        session = self._get_db_session()
        try:
            exec_row = session.get(ExecutionContextModel, ctx.execution_id)
            exec_token = exec_row.exec_token if exec_row else None
        finally:
            session.close()
        if exec_token:
            payload["exec_token"] = exec_token
        return payload

    def _register_worker_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        # The bootstrap token is a shared secret validated on every registration;
        # without a rate limit it is online-brute-forceable at full request rate.
        register_rate_limit = Configuration.get().settings.workers.register_rate_limit

        def _register_limited(fn):
            return limiter.limit(register_rate_limit)(fn) if register_rate_limit else fn

        @api.post("/admin/workers/join-tokens")
        async def mint_join_token(
            body: dict | None = Body(None),
            identity: FluxIdentity = Depends(require_permission("admin:workers:manage")),
        ):
            """Mint a one-time worker join token (SEC3).

            The plaintext is returned exactly once; only its hash is stored.
            Hand it to one new worker as its registration Bearer token — it
            is consumed on first use and expires after the TTL.
            """
            from flux.security import join_tokens

            workers_config = Configuration.get().settings.workers
            try:
                raw_ttl = (body or {}).get("ttl_seconds")
                # None means "not provided" — an explicit 0 must reach mint()
                # and be rejected there, not silently become the default.
                ttl = workers_config.join_token_ttl if raw_ttl is None else int(raw_ttl)
                token, expires_at = await asyncio.to_thread(
                    join_tokens.mint,
                    ttl,
                    created_by=identity.subject,
                )
            except (TypeError, ValueError) as e:
                raise HTTPException(status_code=400, detail=str(e))
            from datetime import timezone as _tz

            return {
                "token": token,
                "expires_at": expires_at.replace(tzinfo=_tz.utc).isoformat(),
            }

        @api.post("/workers/register")
        @_register_limited
        async def workers_register(
            request: Request,
            registration: WorkerRegistration = Body(...),
            authorization: str = Header(None),
        ):
            try:
                logger.debug(f"Worker registration request: {registration.name}")
                token = self._extract_token(authorization)
                expected = self._bootstrap_token
                workers_config = Configuration.get().settings.workers

                # Two accepted credentials: the shared bootstrap token (unless
                # the fleet has migrated off it) or a one-time join token,
                # consumed atomically so it cannot be replayed.
                authorized = bool(
                    workers_config.bootstrap_token_enabled
                    and expected
                    and token
                    and hmac.compare_digest(expected, token),
                )
                if not authorized and token:
                    from flux.security import join_tokens

                    authorized = await asyncio.to_thread(
                        join_tokens.claim,
                        token,
                        registration.name,
                    )
                if not authorized:
                    logger.warning(f"Invalid registration token for worker: {registration.name}")
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid bootstrap or join token.",
                    )

                # Quarantine wins over any valid credential: a banned worker
                # principal must not resurrect itself by re-registering (the
                # re-enable below is meant for reaper-disabled principals of
                # workers that legitimately return).
                if principal_registry is not None:
                    banned_check = await asyncio.to_thread(
                        principal_registry.find,
                        subject=registration.name,
                        external_issuer="flux",
                    )
                    if banned_check is not None and banned_check.banned:
                        logger.warning(
                            f"Refusing registration for banned worker principal: "
                            f"{registration.name}",
                        )
                        raise HTTPException(
                            status_code=403,
                            detail="Worker principal is banned; an administrator "
                            "must unban and enable it before it can register.",
                        )

                registry = WorkerRegistry.create()
                result = registry.register(
                    registration.name,
                    registration.runtime,
                    registration.packages,
                    registration.resources,
                    labels=registration.labels,
                    max_concurrent_executions=registration.max_concurrent_executions,
                    runners=registration.runners,
                )

                # SQLite is supported for single-node mode only (one server +
                # one worker on the same host, or inline workflow.run). The
                # dispatcher, LISTEN/NOTIFY signal plane, and fencing assume
                # PostgreSQL semantics under concurrency.
                database_url = Configuration.get().settings.database_url or ""
                if database_url.startswith("sqlite") and len(registry.list()) > 1:
                    logger.warning(
                        f"Worker '{registration.name}' registered as an additional "
                        f"worker on a SQLite database. SQLite is supported for "
                        f"single-node mode only — multi-worker fleets require "
                        f"PostgreSQL; expect lock contention and dispatch "
                        f"anomalies otherwise.",
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
                    from datetime import timedelta

                    key_ttl = auth_config.api_keys.worker_key_ttl
                    api_key = await auth_service.create_api_key(
                        principal.id,
                        key_name=f"worker-{registration.name}",
                        # Expiring keys rotate themselves: workers re-register
                        # on the first 401 after expiry and get a fresh key.
                        expires=timedelta(seconds=key_ttl) if key_ttl else None,
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
                self._worker_unhealthy.discard(registration.name)
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
            payload: dict | None = Body(None),
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            """Receive heartbeat pong from a worker.

            The optional body carries self-health ({"healthy": bool}) and
            advertised metrics ({"metrics": {str: float}}): unhealthy workers
            stay connected (running work finishes, the reaper is not
            involved) but are excluded from new dispatch until they report
            healthy again; metrics feed routing policies through "metric:*"
            selectors. Legacy workers send no body.
            """
            try:
                self._verify_worker_identity(identity, name)
                await self._record_heartbeat(name)
                if payload is not None and "metrics" in payload:
                    await self._record_worker_metrics(name, payload.get("metrics"))
                healthy = True if payload is None else bool(payload.get("healthy", True))
                if healthy:
                    if name in self._worker_unhealthy:
                        logger.info(f"Worker {name} reports healthy again; resuming dispatch")
                    self._worker_unhealthy.discard(name)
                elif name not in self._worker_unhealthy:
                    logger.warning(
                        f"Worker {name} reports unhealthy (event-loop starvation); "
                        f"excluding it from dispatch until it recovers",
                    )
                    self._worker_unhealthy.add(name)
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
                self._worker_offline_since.pop(name, None)
                self._worker_unhealthy.discard(name)
                await self._record_heartbeat(name)
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

                self._worker_info[name] = worker
                dispatch_mode = Configuration.get().settings.dispatch.mode
                if dispatch_mode == "event":
                    # A reconnect supersedes any previous stream for this worker:
                    # release frames the old queue never delivered, then install
                    # the new queue the dispatcher will feed.
                    self._drain_worker_queue(name)
                    frame_queue: asyncio.Queue = asyncio.Queue()
                    self._worker_queues[name] = frame_queue
                    # New capacity — let the dispatcher assign any pending work.
                    self._work_available.set()

                    async def consume_dispatch_queue():
                        last_ping_time = time.monotonic()
                        try:
                            while True:
                                if eviction_event.is_set():
                                    logger.info(f"Worker {name} evicted by reaper, closing SSE")
                                    return

                                now = time.monotonic()
                                if (now - last_ping_time) >= self.heartbeat_interval:
                                    last_ping_time = now
                                    yield {"event": "ping", "data": ""}

                                try:
                                    item = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
                                except TimeoutError:
                                    continue
                                logger.debug(
                                    f"Sending {item.kind} to worker {name}: {item.execution_id}",
                                )
                                yield item.frame
                        finally:
                            if self._worker_connection_gen.get(name) == gen:
                                self._disconnect_worker(name)
                                logger.info(
                                    f"Worker {name} disconnected "
                                    f"(remaining: {len(self._worker_names)})",
                                )
                            else:
                                logger.debug(
                                    f"Stale SSE for {name} closed (superseded by newer connection)",
                                )

                    return EventSourceResponse(
                        consume_dispatch_queue(),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
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

                                # Self-reported unhealthy (event-loop lag):
                                # skip new work, keep pings/cancellations.
                                if name in self._worker_unhealthy:
                                    await asyncio.sleep(fallback_interval)
                                    continue

                                ctx = await asyncio.to_thread(
                                    context_manager.next_execution,
                                    worker,
                                )
                                if ctx:
                                    payload_dict = await asyncio.to_thread(
                                        self._build_dispatch_payload,
                                        ctx,
                                    )

                                    logger.debug(
                                        f"Sending execution to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    data_payload = _inject_trace_context(to_json(payload_dict))

                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_scheduled",
                                        "data": data_payload,
                                    }
                                    logger.debug(
                                        f"Execution {ctx.execution_id} scheduled for worker {name}",
                                    )
                                    continue

                                ctx = await asyncio.to_thread(
                                    context_manager.next_cancellation,
                                    worker,
                                )
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

                                ctx = await asyncio.to_thread(context_manager.next_resume, worker)
                                if ctx:
                                    resume_payload_dict = await asyncio.to_thread(
                                        self._build_dispatch_payload,
                                        ctx,
                                    )
                                    logger.debug(
                                        f"Sending resume to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )
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
            response: Response,
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
                    current = await asyncio.to_thread(context_manager.get, execution_id)
                except ExecutionContextNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution {execution_id} not found.",
                    )

                from flux.errors import ExecutionError

                if current.state in (ExecutionState.CREATED, ExecutionState.SCHEDULED):
                    ctx = await asyncio.to_thread(context_manager.claim, execution_id, worker)
                    is_resume_claim = False
                elif current.state == ExecutionState.RESUME_SCHEDULED:
                    try:
                        ctx = await asyncio.to_thread(
                            context_manager.claim_resume,
                            execution_id,
                            worker,
                        )
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

                # Fencing token: the worker echoes this on every checkpoint so
                # a superseded claim (unclaimed after partition, reassigned)
                # can be rejected instead of interleaving with the new owner.
                generation = await asyncio.to_thread(
                    context_manager.get_claim_generation,
                    execution_id,
                )
                response.headers["X-Flux-Claim-Generation"] = str(generation)

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
            claim_generation: str | None = Header(None, alias="X-Flux-Claim-Generation"),
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

                expected_generation = None
                if claim_generation is not None:
                    try:
                        expected_generation = int(claim_generation)
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid X-Flux-Claim-Generation header.",
                        )

                try:
                    ctx = await asyncio.to_thread(
                        context_manager.update,
                        domain_ctx,
                        expected_generation,
                    )
                except StaleClaimError as e:
                    logger.warning(str(e))
                    raise HTTPException(status_code=409, detail=f"stale-claim: {e}")
                except ExecutionContextNotFoundError:
                    logger.warning(f"Execution context not found: {execution_id}")
                    raise HTTPException(status_code=404, detail="Execution context not found.")
                logger.debug(f"Checkpoint saved for {execution_id}, state: {ctx.state.value}")

                if ctx.has_finished:
                    self._execution_queue_times.pop(execution_id, None)
                    # A finished execution frees one of the worker's capacity
                    # slots — wake dispatch so any work that was held back by
                    # a full fleet gets assigned now.
                    self._notify_next_worker()

                # Notify any waiting sync/stream endpoint — locally, and on
                # other replicas via NOTIFY when the state is one a caller
                # blocks on (the waiter re-reads the row either way).
                event = self._execution_events.get(execution_id)
                if event:
                    event.set()
                if self._dispatcher is not None and (ctx.has_finished or ctx.is_paused):
                    self._dispatcher.notify_execution_update(execution_id)

                return ctx.summary()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error checkpointing execution: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))

        @api.post("/workers/{name}/release/{execution_id}")
        async def workers_release(
            name: str,
            execution_id: str,
            claim_generation: str | None = Header(None, alias="X-Flux-Claim-Generation"),
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            """Hand a claimed execution back for re-dispatch.

            Called by a worker whose runner child crashed mid-execution: the
            events checkpointed before the crash are already persisted, so a
            re-dispatched durable execution resumes via deterministic replay.
            Fenced by claim generation like checkpoints — a stale claimant
            gets a 409 instead of unclaiming the new owner's execution.
            """
            try:
                self._verify_worker_identity(identity, name)
                self._worker_last_pong[name] = time.monotonic()

                context_manager = ContextManager.create()

                if claim_generation is not None:
                    try:
                        expected_generation = int(claim_generation)
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid X-Flux-Claim-Generation header.",
                        )
                    current_generation = await asyncio.to_thread(
                        context_manager.get_claim_generation,
                        execution_id,
                    )
                    if expected_generation != current_generation:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"stale-claim: release carries generation "
                                f"{expected_generation} but the row is at "
                                f"{current_generation}"
                            ),
                        )

                try:
                    ctx = await asyncio.to_thread(context_manager.unclaim, execution_id)
                except ExecutionContextNotFoundError:
                    raise HTTPException(status_code=404, detail="Execution context not found.")

                logger.warning(
                    f"Worker {name} released execution {execution_id} "
                    f"(state now {ctx.state.value}); re-dispatching",
                )
                self._notify_next_worker()
                return {"status": "released", "state": ctx.state.value}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error releasing execution {execution_id}: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))

        @api.get("/workers/{name}/approvals/{execution_id}/{task_call_id}")
        async def workers_approval_get(
            name: str,
            execution_id: str,
            task_call_id: str,
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            """Approval-row lookup for the worker-side approval gate.

            Workers (and runner children, via their parent worker) never
            read approval rows from the database — the server owns it.
            """
            from flux.approvals import ApprovalManager, ApprovalSnapshot

            self._verify_worker_identity(identity, name)
            row = await asyncio.to_thread(
                lambda: ApprovalManager().get_by_call(execution_id, task_call_id),
            )
            return {"approval": ApprovalSnapshot.from_model(row).to_dict() if row else None}

        @api.post("/workers/{name}/approvals/{execution_id}")
        async def workers_approval_register(
            name: str,
            execution_id: str,
            payload: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("worker:*:*")),
        ):
            """Register the approval row for a gated task call.

            Normally creates the PENDING row (status "created"); when a
            standing grant covers the task, materializes an APPROVED row
            instead (status "granted") so the worker's gate reads it back
            approved and never pauses. Other statuses: "exists",
            "cancelled". The pausability check and the row insert run in
            one transaction with the execution row locked, so a concurrent
            cancel either wins (status "cancelled") or sweeps the row
            afterwards via cancel_pending_for_execution. The
            TASK_AWAITING_APPROVAL event arrives through the normal
            checkpoint path.
            """
            self._verify_worker_identity(identity, name)
            task_call_id = payload.get("task_call_id")
            task_name = payload.get("task_name")
            if not task_call_id or not task_name:
                raise HTTPException(
                    status_code=400,
                    detail="task_call_id and task_name are required.",
                )

            def _register() -> str:
                from sqlalchemy import select

                from flux.approvals import ApprovalManager
                from flux.domain import ExecutionState
                from flux.models import ExecutionContextModel
                from flux.unit_of_work import UnitOfWork

                mgr = ApprovalManager()
                if mgr.get_by_call(execution_id, task_call_id) is not None:
                    return "exists"
                grant = mgr.find_standing_grant(execution_id, task_name)
                with UnitOfWork() as uow:
                    model = uow.session.execute(
                        select(ExecutionContextModel)
                        .where(ExecutionContextModel.execution_id == execution_id)
                        .with_for_update(),
                    ).scalar_one_or_none()
                    if model is None:
                        return "not_found"
                    pausable = (
                        ExecutionState.CLAIMED,
                        ExecutionState.RUNNING,
                        ExecutionState.RESUME_CLAIMED,
                        ExecutionState.RESUMING,
                    )
                    # The grant path shares the guard: a concurrent cancel
                    # that already made the execution non-pausable must not
                    # be raced by an auto-approved row that lets the gated
                    # body run anyway.
                    if model.state not in pausable:
                        return "cancelled"
                    if grant is not None:
                        # Standing grant ("approve always" for this
                        # execution): materialize an approved row so the
                        # worker's gate reads it back approved and never
                        # pauses.
                        mgr.create_granted(
                            execution_id=execution_id,
                            task_call_id=task_call_id,
                            workflow_namespace=model.workflow_namespace,
                            workflow_name=model.workflow_name,
                            task_name=task_name,
                            grant=grant,
                            uow=uow,
                        )
                        uow.commit()
                        return "granted"
                    mgr.create(
                        execution_id=execution_id,
                        task_call_id=task_call_id,
                        workflow_namespace=model.workflow_namespace,
                        workflow_name=model.workflow_name,
                        task_name=task_name,
                        uow=uow,
                    )
                    uow.commit()
                return "created"

            try:
                status = await asyncio.to_thread(_register)
            except Exception as e:
                logger.error(f"Error registering approval for {execution_id}: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))
            if status == "not_found":
                raise HTTPException(status_code=404, detail="Execution context not found.")
            return {"status": status}

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

            try:
                ctx = ContextManager.create().get(execution_id)
            except ExecutionContextNotFoundError:
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
                metrics=getattr(w, "metrics", None),
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
            limit: int | None = Query(None, ge=1, le=1000),
            offset: int = Query(0, ge=0),
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List workers fleet-wide. Optional ?status=online|offline filter.

            Reads the workers table rather than this replica's in-memory cache,
            so every replica returns the same, complete fleet view; liveness is
            derived from the persisted heartbeat (a worker is online while its
            last_seen_at is within heartbeat_timeout + eviction grace, matching
            the reaper's staleness window).

            Worker visibility is intentionally unpermissioned — any authenticated user
            may discover available workers. Sensitive details are not exposed.
            """
            try:
                logger.debug(f"Listing workers (filter={status})")

                registry = WorkerRegistry.create()
                infos = await asyncio.to_thread(registry.list)

                from datetime import datetime, timedelta, timezone

                threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    seconds=self.heartbeat_timeout + self.eviction_grace_period,
                )

                def _status(info) -> str:
                    # This replica knows its own connections authoritatively —
                    # and immediately (a locally-disconnected worker must not
                    # read "online" for the rest of its heartbeat window). The
                    # persisted-heartbeat fallback covers workers attached to
                    # OTHER replicas, which this process cannot see directly.
                    if info.name in self._worker_unhealthy:
                        return "unhealthy"
                    if info.name in self._worker_names:
                        return "online"
                    if info.name in self._worker_offline_since:
                        return "offline"
                    last_seen = getattr(info, "last_seen_at", None)
                    return (
                        "online" if last_seen is not None and last_seen >= threshold else "offline"
                    )

                result = []
                for info in infos:
                    worker_status = _status(info)
                    if status is not None and worker_status != status:
                        continue
                    cached = self._worker_cache.get(info.name)
                    if cached is not None:
                        cached.status = worker_status
                        cached.metrics = info.metrics
                        result.append(cached)
                    else:
                        result.append(
                            WorkerResponse(
                                name=info.name,
                                status=worker_status,
                                metrics=info.metrics,
                            ),
                        )

                logger.debug(f"Found {len(result)} workers")
                # Deterministic order so offset pagination yields stable,
                # non-overlapping pages (the DB query has no ORDER BY).
                result.sort(key=lambda w: w.name)
                # Opt-in pagination: unpaginated calls keep the full-list
                # contract, large fleets can bound the response.
                if offset:
                    result = result[offset:]
                if limit is not None:
                    result = result[:limit]
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
