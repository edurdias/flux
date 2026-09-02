from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from typing import Any
from collections.abc import AsyncIterator, Callable

import uvicorn
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from flux import ExecutionContext
from flux.execution_signals import ExecutionSignals
from flux.execution_start import create_execution
from flux.execution_stream import stream_execution_events
from flux.hooks.dispatch import authorize_hook_principal, start_hook_execution
from flux.scheduler_loop import SchedulerLoop
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.utils import get_logger
from flux.servers.uvicorn_server import UvicornServer
from flux.security.auth_service import AuthService
from flux.security.dependencies import init_auth_service
from flux.security.identity import FluxIdentity
from flux.api.auth_routes import AuthRoutesMixin
from flux.api.system_routes import SystemRoutesMixin
from flux.api.workflow_routes import WorkflowRoutesMixin
from flux.api.worker_routes import WorkerRoutesMixin
from flux.api.admin_routes import AdminRoutesMixin
from flux.api.schedule_routes import ScheduleRoutesMixin
from flux.api.hook_routes import HookRoutesMixin
from flux.api.execution_routes import ExecutionRoutesMixin
from flux.api.dynamic_routes import DynamicRoutesMixin
from flux.api.service_routes import ServiceRoutesMixin
from flux.api.rbac_routes import RbacRoutesMixin
from datetime import datetime, timedelta, timezone

# Re-exported for backward compatibility (these were defined here before the
# route modules were extracted). flux.api.schemas is the source of truth.
from flux.api.schemas import (  # noqa: F401
    MAX_WORKFLOW_UPLOAD_BYTES as MAX_WORKFLOW_UPLOAD_BYTES,
    SERVICE_NAME_RE as SERVICE_NAME_RE,
    _rate_limit_exceeded_handler as _rate_limit_exceeded_handler,
    _has_any_workflow_read as _has_any_workflow_read,
    _inject_trace_context as _inject_trace_context,
    WorkerRuntimeModel as WorkerRuntimeModel,
    WorkerGPUModel as WorkerGPUModel,
    WorkerResourcesModel as WorkerResourcesModel,
    WorkerRegistration as WorkerRegistration,
    SecretRequest as SecretRequest,
    SecretResponse as SecretResponse,
    ConfigRequest as ConfigRequest,
    ScheduleRequest as ScheduleRequest,
    ScheduleResponse as ScheduleResponse,
    ScheduleUpdateRequest as ScheduleUpdateRequest,
    RoleRequest as RoleRequest,
    RoleUpdateRequest as RoleUpdateRequest,
    RoleCloneRequest as RoleCloneRequest,
    ApprovalDecideRequest as ApprovalDecideRequest,
    APIKeyRequest as APIKeyRequest,
    TestTokenRequest as TestTokenRequest,
    PrincipalCreateRequest as PrincipalCreateRequest,
    PrincipalUpdateRequest as PrincipalUpdateRequest,
    RoleGrantRequest as RoleGrantRequest,
    PrincipalResponse as PrincipalResponse,
    WorkflowVersionResponse as WorkflowVersionResponse,
    ExecutionSummaryResponse as ExecutionSummaryResponse,
    ExecutionListResponse as ExecutionListResponse,
    WorkerResponse as WorkerResponse,
    HealthResponse as HealthResponse,
    AgentSessionSummaryResponse as AgentSessionSummaryResponse,
    AgentSessionListResponse as AgentSessionListResponse,
    ScheduleHistoryEntry as ScheduleHistoryEntry,
    ScheduleHistoryResponse as ScheduleHistoryResponse,
)

# Re-exported for backward compatibility: existing code/tests reference (and
# patch) these via the ``flux.server`` namespace even though the route
# handlers that use them now live in the ``flux.api`` route modules.
from flux.worker_registry import WorkerRegistry as WorkerRegistry  # noqa: F401, E402

logger = get_logger(__name__)


def _install_slow_callback_logging():
    """Arm asyncio's slow-callback warning when the setting asks for it.

    ``[flux.observability] slow_callback_ms`` (env:
    ``FLUX_OBSERVABILITY__SLOW_CALLBACK_MS``).

    A blocking call inside an async handler -- a sync DB round trip, a
    pickle, a file read -- stalls every other request on the loop, and the
    only cheap way to find them is to have asyncio say which callback ran
    long. Python's default threshold is 100ms, which is far above a single
    sync query: the interesting offenders are the 5-20ms ones that are
    individually invisible and collectively the tail latency (#263).

    Returns a uvicorn ``callback_notify`` (called once per second on the
    running loop) rather than touching the loop here, because the loop does
    not exist until uvicorn starts one.
    """
    threshold_ms = Configuration.get().settings.observability.slow_callback_ms
    if not threshold_ms or threshold_ms <= 0:
        return None
    threshold_s = threshold_ms / 1000

    armed = False

    async def _arm() -> None:
        nonlocal armed
        if armed:
            return
        armed = True
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        loop.slow_callback_duration = threshold_s
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logger.warning(
            f"asyncio debug mode on: callbacks over {threshold_s * 1000:.0f}ms will be logged",
        )

    return _arm


def _hook_execution_starter(server: Server) -> Callable[..., Any]:
    """`start_hook_execution` with the server's creation path bound in."""
    return functools.partial(
        start_hook_execution,
        server._create_execution,
        server._get_db_session,
    )


def _hook_authorizer(server: Server) -> Callable[..., Any]:
    """`authorize_hook_principal` with the server's session factory bound in."""
    return functools.partial(authorize_hook_principal, server._get_db_session)


class Server(
    SystemRoutesMixin,
    WorkflowRoutesMixin,
    WorkerRoutesMixin,
    AdminRoutesMixin,
    ScheduleRoutesMixin,
    HookRoutesMixin,
    ExecutionRoutesMixin,
    DynamicRoutesMixin,
    ServiceRoutesMixin,
    RbacRoutesMixin,
    AuthRoutesMixin,
):
    """
    Server for managing workflows and tasks with integrated scheduler.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

        # Scheduler state

        self._work_available = asyncio.Event()
        self._worker_events: dict[str, asyncio.Event] = {}
        self._worker_names: list[str] = []
        self._worker_rr_index = 0
        # Per-execution waiters, progress buffers and queue timing, with one
        # vocabulary instead of three dictionaries poked from four modules
        # (#264 stage 3).
        self.signals = ExecutionSignals()
        self._worker_last_pong: dict[str, float] = {}
        self._worker_cache: dict[str, WorkerResponse] = {}
        self._worker_offline_since: dict[str, float] = {}
        # Workers that self-reported event-loop starvation on their heartbeat
        # pong: still connected (running work finishes) but excluded from new
        # dispatch until they report healthy again.
        self._worker_unhealthy: set[str] = set()
        # Workers that self-reported an operator pause: connected, heartbeats
        # flowing, but claiming nothing — excluded from dispatch like
        # unhealthy workers, yet surfaced as 'paused' (deliberate) rather
        # than 'unhealthy' (a fault).
        self._worker_paused: set[str] = set()
        # Latest in-flight execution count each worker advertised on its
        # heartbeat pong; surfaced in GET /workers.
        self._worker_in_flight: dict[str, int] = {}
        # Last metrics snapshot persisted per worker; pongs repeat the current
        # snapshot every beat, so this gate keeps DB writes on the (slower)
        # metrics-refresh cadence instead of the heartbeat rate.
        self._worker_metrics_persisted: dict[str, dict[str, float]] = {}
        self._worker_evicted: dict[str, asyncio.Event] = {}
        self._worker_stale_since: dict[str, float] = {}
        # Resolved by the FastAPI lifespan startup hook in _create_api so that
        # merely constructing the app for tests / OpenAPI does not have the
        # side effect of generating the persisted token file.
        self._bootstrap_token: str | None = None
        self._worker_connection_gen: dict[str, int] = {}

        # Event-dispatch state: per-worker SSE frame queues and the WorkerInfo
        # snapshots the dispatcher matches against. Populated only for workers
        # connected to THIS replica; cross-replica coordination happens through
        # the database (SKIP LOCKED) and LISTEN/NOTIFY wakeups.
        self._worker_queues: dict[str, asyncio.Queue] = {}
        self._worker_info: dict[str, object] = {}
        self._pending_heartbeats: set[str] = set()
        self._dispatch_mode = Configuration.get().settings.dispatch.mode
        self._dispatcher = None
        self._retention_job = None
        self._reaper_task: asyncio.Task | None = None
        self._scheduler_loop_obj: SchedulerLoop | None = None

        config = Configuration.get().settings.scheduling
        self.poll_interval = config.poll_interval

        workers_config = Configuration.get().settings.workers
        self.heartbeat_interval = workers_config.heartbeat_interval
        self.heartbeat_timeout = workers_config.heartbeat_timeout
        self.offline_ttl = workers_config.offline_ttl
        self.eviction_grace_period = workers_config.eviction_grace_period

        try:
            from flux.observability import setup as setup_observability

            obs_config = Configuration.get().settings.observability
            setup_observability(obs_config)
        except ImportError:
            logger.debug("Observability packages not installed, skipping setup")
        except Exception:
            logger.warning("Observability setup failed", exc_info=True)

    # Backward compatibility: stage 3 moved these three dictionaries into
    # ExecutionSignals. Tests and external code still access them directly on
    # Server; expose them as proxies so the old path keeps working. The
    # __new__-without-__init__ case (test_server_has_progress_buffers_dict)
    # must also work, so fall back to instance dict when signals is absent.
    @property
    def _execution_events(self) -> dict[str, asyncio.Event]:
        if "signals" in self.__dict__:
            return self.signals._events
        return self.__dict__.get("_execution_events", {})

    @_execution_events.setter
    def _execution_events(self, value: dict[str, asyncio.Event]) -> None:
        if "signals" in self.__dict__:
            self.signals._events = value
        else:
            self.__dict__["_execution_events"] = value

    @property
    def _progress_buffers(self) -> dict[str, asyncio.Queue]:
        if "signals" in self.__dict__:
            return self.signals._progress_buffers
        return self.__dict__.get("_progress_buffers", {})

    @_progress_buffers.setter
    def _progress_buffers(self, value: dict[str, asyncio.Queue]) -> None:
        if "signals" in self.__dict__:
            self.signals._progress_buffers = value
        else:
            self.__dict__["_progress_buffers"] = value

    @property
    def _execution_queue_times(self) -> dict[str, float]:
        if "signals" in self.__dict__:
            return self.signals._queued_at
        return self.__dict__.get("_execution_queue_times", {})

    @_execution_queue_times.setter
    def _execution_queue_times(self, value: dict[str, float]) -> None:
        if "signals" in self.__dict__:
            self.signals._queued_at = value
        else:
            self.__dict__["_execution_queue_times"] = value

    def _scheduler(self) -> SchedulerLoop:
        """The scheduler tick, built on first use.

        Built here rather than in __init__ because it takes the hook
        callables, which bind this server's own creation path -- see
        flux/scheduler_loop.py for what it needs and why.
        """
        if self._scheduler_loop_obj is None:
            self._scheduler_loop_obj = SchedulerLoop(
                create_execution=self._create_execution,
                session_factory=self._get_db_session,
                signals=self.signals,
                worker_queues=self._worker_queues,
                hook_starter=_hook_execution_starter(self),
                hook_authorizer=_hook_authorizer(self),
                poll_interval=self.poll_interval,
            )
        return self._scheduler_loop_obj

    def _get_db_session(self):
        from flux.models import RepositoryFactory

        repo = RepositoryFactory.create_repository()
        return repo.session()

    def _drain_worker_queue(self, name: str) -> None:
        """Release executions whose dispatch frames were never delivered.

        A disconnecting worker may leave assigned-but-unsent frames in its SSE
        queue. Unclaim those executions right away — instead of waiting the
        ~60s eviction path — so the dispatcher can reassign them. Cancellation
        frames are simply dropped: the row is still CANCELLING and will be
        re-delivered wherever the execution lands.

        Draining the queue is pure memory work; the unclaim writes are DB
        round-trips and run in a background thread hop so a slow database (or
        a long queue) never blocks the event loop mid-SSE-teardown.
        """
        queue = self._worker_queues.pop(name, None)
        if queue is None:
            return
        to_release: list[str] = []
        while not queue.empty():
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if getattr(item, "kind", None) in ("execution_scheduled", "execution_resumed"):
                to_release.append(item.execution_id)
        if not to_release:
            return

        def _release() -> int:
            manager = ContextManager.create()
            released = 0
            for execution_id in to_release:
                try:
                    manager.unclaim(execution_id)
                    released += 1
                except Exception:
                    logger.error(
                        f"Failed to release undelivered execution {execution_id}",
                        exc_info=True,
                    )
            return released

        async def _release_and_notify():
            released = await asyncio.to_thread(_release)
            if released:
                logger.info(f"Released {released} undelivered execution(s) from worker {name}")
                self._work_available.set()

        try:
            asyncio.get_running_loop()
            asyncio.create_task(_release_and_notify())
        except RuntimeError:
            # No loop (tests / teardown): release synchronously.
            if _release():
                logger.info(f"Released undelivered execution(s) from worker {name}")

    def _notify_next_worker(self):
        """Signal that new work is available.

        Poll mode: wake the next connected worker's loop in round-robin order.
        Event mode: wake this replica's dispatcher and NOTIFY other replicas.
        """
        if self._dispatch_mode == "event":
            self._work_available.set()
            if self._dispatcher is not None:
                self._dispatcher.notify_remote_replicas()
            return

        if not self._worker_names:
            self._work_available.set()
            return

        # Try each worker once; if all are gone, fall back to broadcast
        for _ in range(len(self._worker_names)):
            idx = self._worker_rr_index % len(self._worker_names)
            self._worker_rr_index += 1
            name = self._worker_names[idx]
            event = self._worker_events.get(name)
            if event:
                event.set()
                return

        # Fallback: broadcast to all
        self._work_available.set()

    def start(self):
        """
        Start Flux server.
        """
        logger.info(f"Starting Flux server at {self.host}:{self.port}")
        logger.debug(f"Server version: {self._get_version()}")

        async def on_server_startup():
            logger.info("Flux server started successfully")
            logger.debug("Server is ready to accept connections")

            await self._scheduler().start()
            logger.info(f"Scheduler started (poll_interval={self.poll_interval}s)")

            self._reaper_task = asyncio.create_task(self._run_heartbeat_reaper())
            logger.info(
                f"Heartbeat reaper started (interval={self.heartbeat_interval}s, "
                f"timeout={self.heartbeat_timeout}s)",
            )

            if self._dispatch_mode == "event":
                from flux.dispatcher import Dispatcher

                self._dispatcher = Dispatcher(self)
                self._dispatcher.start()

            if Configuration.get().settings.retention.enabled:
                from flux.retention import RetentionJob

                self._retention_job = RetentionJob()
                self._retention_job.start()

        try:
            from flux.runtime_loop import uvicorn_loop_name

            config = uvicorn.Config(
                self._create_api(),
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
                loop=uvicorn_loop_name(),
                callback_notify=_install_slow_callback_logging(),
            )
            server = UvicornServer(config, on_server_startup)
            server.run()
        except Exception as e:
            logger.error(f"Error starting Flux server: {str(e)}")
            raise
        finally:
            logger.info("Flux server stopped")
            logger.debug("Server shutdown complete")

    def _extract_token(self, authorization: str | None) -> str:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authorization header missing")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization format")
        # Match flux.security.dependencies.get_identity: split exactly once so a
        # token containing spaces parses correctly, and strip surrounding
        # whitespace so a header like "Bearer  abc " yields "abc" rather than "".
        parts = authorization.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 else ""
        if not token:
            raise HTTPException(status_code=401, detail="Invalid authorization format")
        return token

    def _verify_worker_identity(self, identity: FluxIdentity, name: str) -> None:
        auth_config = Configuration.get().settings.security.auth
        if auth_config.enabled and identity.subject != name:
            from flux.observability import get_metrics as _gm_bind

            _m_bind = _gm_bind()
            if _m_bind:
                _m_bind.record_worker_auth_event(name, "identity_mismatch")
            raise HTTPException(
                status_code=403,
                detail=f"Worker identity mismatch: authenticated as '{identity.subject}', "
                f"but accessing endpoint for '{name}'",
            )

    def _verify_worker_owns_execution(self, name: str, execution_id: str) -> None:
        """Reject a worker acting on an execution it does not hold the claim for.

        ``_verify_worker_identity`` proves only that the caller is the worker
        it claims to be. Routes that act on a specific execution need this in
        addition, because the execution is named by the request rather than by
        the credential — the pattern ``workers_secrets_batch`` already applies.
        """
        from flux.context_managers import ContextManager
        from flux.errors import ExecutionContextNotFoundError

        try:
            ctx = ContextManager.create().get(execution_id)
        except ExecutionContextNotFoundError:
            # Only a genuinely absent execution is a 404; a database or
            # catalog failure must surface as a 500 rather than be reported
            # as "not found".
            raise HTTPException(status_code=404, detail="Execution not found") from None
        # Only a claim held by someone *else* is a cross-execution write. An
        # unclaimed execution is the recovery path — eviction clears
        # worker_name and bumps claim_generation so the old owner's next
        # checkpoint is fenced with 409 stale-claim, which the worker handles
        # by abandoning cleanly. Rejecting it here with 403 pre-empts that
        # fence and strands the execution in CREATED.
        if ctx.current_worker and ctx.current_worker != name:
            raise HTTPException(
                status_code=403,
                detail=f"Worker '{name}' does not hold the claim for execution '{execution_id}'",
            )

    def _get_version(self) -> str:
        import importlib.metadata

        try:
            version = importlib.metadata.version("flux-core")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"  # Default if package is not installed
        return version

    def _get_title(self) -> str:
        import importlib.metadata

        try:
            metadata = importlib.metadata.metadata("flux-core")
            # Use the description as title, or fall back to name
            title = metadata.get("Summary") or metadata.get("Name", "Flux")
            return f"{title} API"
        except importlib.metadata.PackageNotFoundError:
            return "Flux API"  # Default if package is not installed

    # ===========================================
    # Auto-Scheduling Helper
    # ===========================================

    def _create_execution(
        self,
        namespace: str,
        workflow_name: str,
        input_data: Any = None,
        version: int | None = None,
        preferred_worker: str | None = None,
        required_worker: str | None = None,
        routing_input: dict | None = None,
        park_ttl: int | None = None,
        name: str | None = None,
    ) -> ExecutionContext:
        return create_execution(
            self.signals,
            namespace,
            workflow_name,
            input_data=input_data,
            version=version,
            preferred_worker=preferred_worker,
            required_worker=required_worker,
            routing_input=routing_input,
            park_ttl=park_ttl,
            name=name,
        )

    def _stream_execution_events(
        self,
        ctx: ExecutionContext,
        manager: ContextManager,
        detailed: bool,
        emit_initial: bool = False,
    ) -> AsyncIterator[dict]:
        return stream_execution_events(
            self.signals,
            ctx,
            manager,
            detailed,
            emit_initial=emit_initial,
        )

    # ===========================================
    # Integrated Scheduler Methods
    # ===========================================

    async def _stop_reaper(self):
        """Stop the heartbeat reaper task."""
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
            # Persist any pongs buffered since the last tick so the fleet's
            # liveness view stays fresh across a server restart.
            await self._flush_heartbeats()
            logger.info("Heartbeat reaper stopped")

    def _persist_worker_heartbeat(self, name: str) -> None:
        from flux.worker_registry import WorkerRegistry

        WorkerRegistry.create().record_heartbeat(name)

    async def _record_heartbeat(self, name: str) -> None:
        """Record a worker heartbeat: in-memory fast-path + persisted timestamp.

        The in-memory ``_worker_last_pong`` drives this replica's round-robin
        and local stale tracking; the persisted ``last_seen_at`` gives every
        replica's reaper a global view of liveness so orphaned executions can
        be reclaimed when the replica a worker was attached to dies.

        While the reaper is running, persistence is batched: pongs buffer in
        memory and the reaper tick flushes them as ONE statement per interval
        — at fleet scale this replaces one commit per worker per interval.
        The persist delay is at most one heartbeat interval, well inside the
        cross-replica staleness threshold (heartbeat_timeout + grace). Without
        a reaper (embedded/test use) each heartbeat persists immediately.
        """
        self._worker_last_pong[name] = time.monotonic()
        self._worker_stale_since.pop(name, None)
        if self._reaper_task is not None and not self._reaper_task.done():
            self._pending_heartbeats.add(name)
            return
        try:
            await asyncio.to_thread(self._persist_worker_heartbeat, name)
        except Exception as e:
            logger.debug(f"Failed to persist heartbeat for worker {name}: {e}")

    async def _record_worker_metrics(self, name: str, raw: Any) -> None:
        """Store a worker's advertised metrics: in-memory for this replica's
        dispatcher, persisted (on change only) for GET /workers fleet-wide.

        Metrics are a hint channel: an invalid payload is dropped with a
        warning, never an error. Providers refresh every metrics_interval,
        so change-driven persistence is bounded by that cadence — not the
        (much faster) heartbeat rate.
        """
        from flux.routing import MAX_TOTAL_METRICS, validate_worker_metrics

        # Total cap: provider output (bounded worker-side) + built-in flux.*.
        metrics = validate_worker_metrics(raw, max_keys=MAX_TOTAL_METRICS)
        if metrics is None:
            logger.warning(f"Worker {name} sent an invalid metrics payload; ignored")
            return
        info = self._worker_info.get(name)
        if info is not None:
            # _worker_info values are WorkerInfo (typed dict[str, object]).
            setattr(info, "metrics", metrics)
        if self._worker_metrics_persisted.get(name) == metrics:
            return
        try:
            from flux.worker_registry import WorkerRegistry

            registry = WorkerRegistry.create()
            await asyncio.to_thread(registry.record_metrics, name, metrics)
            # Gate only after the write lands: a failed persist must be
            # retried by the next (identical) pong, or GET /workers would
            # stay stale until the values happen to change.
            self._worker_metrics_persisted[name] = metrics
        except Exception as e:
            logger.debug(f"Failed to persist metrics for worker {name}: {e}")

    async def _flush_heartbeats(self) -> None:
        """Persist all buffered pongs in one batched UPDATE."""
        if not self._pending_heartbeats:
            return
        names = list(self._pending_heartbeats)
        self._pending_heartbeats.clear()
        try:
            from flux.worker_registry import WorkerRegistry

            registry = WorkerRegistry.create()
            await asyncio.to_thread(registry.record_heartbeats, names)
        except Exception as e:
            logger.debug(f"Failed to persist {len(names)} heartbeat(s): {e}")

    def _disconnect_worker(self, name: str, reason: str = "disconnect") -> None:
        """Remove a worker from the connected set and mark it offline in cache."""
        self._worker_events.pop(name, None)
        self._worker_last_pong.pop(name, None)
        self._worker_info.pop(name, None)
        self._drain_worker_queue(name)
        if name in self._worker_names:
            self._worker_names.remove(name)
        # Unconditional: a lingering unhealthy/paused flag would wrongly
        # surface in GET /workers even after the worker is gone.
        self._worker_unhealthy.discard(name)
        self._worker_paused.discard(name)
        self._worker_in_flight.pop(name, None)
        self._worker_metrics_persisted.pop(name, None)
        self._worker_offline_since[name] = time.monotonic()
        if name in self._worker_cache:
            self._worker_cache[name].status = "offline"
        evicted = self._worker_evicted.pop(name, None)
        if evicted:
            evicted.set()

        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_worker_disconnected(name, reason)

        if reason == "evicted":
            from flux.security.dependencies import _get_auth_service
            from flux.security.principals import PrincipalRegistry

            _auth_svc = _get_auth_service()
            if _auth_svc is not None:

                async def _revoke_worker_key():
                    try:
                        registry = PrincipalRegistry(session_factory=self._get_db_session)
                        principal = registry.find(subject=name, external_issuer="flux")
                        if principal:
                            await _auth_svc.revoke_all_api_keys(principal.id)
                            logger.info(f"Revoked API key for evicted worker {name}")

                            from flux.observability import get_metrics as _gm_evict

                            _m_evict = _gm_evict()
                            if _m_evict:
                                _m_evict.record_worker_auth_event(
                                    name,
                                    "key_revoked",
                                )
                    except Exception as e:
                        logger.warning(f"Failed to revoke API key for worker {name}: {e}")

                try:
                    asyncio.create_task(_revoke_worker_key())
                except RuntimeError:
                    logger.warning(f"Cannot revoke API key for {name}: no event loop")

    def _gc_worker_principal(self, name: str) -> asyncio.Task | None:
        """Disable a pruned worker's principal and revoke its API keys.

        Runs as a fire-and-forget task off the reaper. Registration re-enables
        the principal if the worker ever returns, so this is reversible.
        Returns the task (None when there is nothing to do or no loop) so a
        caller that needs completion — tests — can await it instead of
        sleeping and hoping.
        """
        from flux.security.dependencies import _get_auth_service
        from flux.security.principals import PrincipalRegistry

        auth_service = _get_auth_service()
        if auth_service is None:
            return None

        async def _gc():
            try:

                def _find_and_disable():
                    registry = PrincipalRegistry(session_factory=self._get_db_session)
                    principal = registry.find(subject=name, external_issuer="flux")
                    if principal and principal.enabled:
                        registry.set_enabled(principal.id, False)
                    return principal

                principal = await asyncio.to_thread(_find_and_disable)
                if principal:
                    await auth_service.revoke_all_api_keys(principal.id)
                    logger.info(f"Disabled principal and revoked keys for pruned worker {name}")
            except Exception as e:
                logger.warning(f"Principal GC for pruned worker {name} failed: {e}")

        try:
            return asyncio.create_task(_gc())
        except RuntimeError:
            logger.warning(f"Cannot GC principal for {name}: no event loop")
            return None

    def _unclaim_worker_executions(self, worker_name: str) -> None:
        """Recover all executions assigned to an evicted worker.

        Queries the DB directly instead of relying on in-memory tracking,
        so dispatched-but-not-yet-claimed executions are also recovered.
        """
        context_manager = ContextManager.create()
        executions = context_manager.find_by_worker(worker_name)
        if not executions:
            return

        from flux.domain import ExecutionState
        from flux.observability import get_metrics

        for ctx in executions:
            try:
                unclaimed = context_manager.unclaim(ctx.execution_id)
                if unclaimed.state in (ExecutionState.PAUSED, ExecutionState.RESUMING):
                    context_manager.release_worker(ctx.execution_id)
                self.signals.stamp_queued(ctx.execution_id, time.monotonic())
                m = get_metrics()
                if m:
                    m.record_execution_queued()
                    if ctx.state in (
                        ExecutionState.RESUMING,
                        ExecutionState.RESUME_SCHEDULED,
                        ExecutionState.RESUME_CLAIMED,
                    ):
                        m.record_resume_queued(
                            ctx.workflow_namespace,
                            ctx.workflow_name,
                        )
                logger.info(
                    f"Unclaimed execution {ctx.execution_id} from evicted worker {worker_name}",
                )
                event = self.signals.event(ctx.execution_id)
                if event:
                    event.set()
            except Exception as e:
                logger.error(f"Failed to unclaim execution {ctx.execution_id}: {e}")

        self._work_available.set()

    async def _reclaim_orphaned_executions(self) -> None:
        """Reclaim executions stranded by a dead replica (cross-replica sweep).

        The local stale/evict path above only sees workers attached to *this*
        replica. If the replica a worker was attached to dies, no local reaper
        knows the worker is gone. This sweep reads the persisted ``last_seen_at``
        so any surviving replica can detect a globally-stale worker — one no
        replica has heard from for the full stale-plus-grace window — and
        reclaim its executions.

        Workers connected to this replica are skipped (the local path owns them).
        ``unclaim`` converges idempotently, so it is safe if several replicas
        run this sweep concurrently for the same orphan.
        """
        deadline_seconds = self.heartbeat_timeout + self.eviction_grace_period
        # Naive UTC to match the stored last_seen_at values (see
        # WorkerRegistry.record_heartbeat).
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=deadline_seconds,
        )
        try:
            from flux.worker_registry import WorkerRegistry

            registry = WorkerRegistry.create()
            stale = await asyncio.to_thread(registry.find_stale, threshold)
        except Exception as e:
            logger.debug(f"Orphan reclaim sweep failed to query stale workers: {e}")
            return

        locally_connected = set(self._worker_names)
        for name in stale:
            if name in locally_connected:
                continue
            try:
                # Called directly on the event loop (not via to_thread) because
                # it sets asyncio.Events (signals, _work_available),
                # which are not thread-safe. This mirrors the local eviction
                # path, which also invokes it synchronously.
                self._unclaim_worker_executions(name)
            except Exception as e:
                logger.warning(f"Failed to reclaim executions for orphaned worker {name}: {e}")

    async def _run_heartbeat_reaper(self):
        """Background task: two-phase eviction (stale → grace → evict) and offline cache pruning."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                now = time.monotonic()

                # Persist the interval's buffered pongs first, so cross-replica
                # staleness views are as fresh as possible before we sweep.
                await self._flush_heartbeats()

                for name, last_pong in list(self._worker_last_pong.items()):
                    if (now - last_pong) > self.heartbeat_timeout:
                        if name not in self._worker_stale_since:
                            self._worker_stale_since[name] = now
                            logger.warning(
                                f"Worker {name} missed heartbeat, marked STALE "
                                f"(grace period: {self.eviction_grace_period}s)",
                            )

                recovered = [
                    name
                    for name in list(self._worker_stale_since)
                    if name in self._worker_last_pong
                    and (now - self._worker_last_pong[name]) <= self.heartbeat_timeout
                ]
                for name in recovered:
                    self._worker_stale_since.pop(name, None)
                    logger.info(f"Worker {name} recovered from stale state")

                evicted = [
                    name
                    for name, since in list(self._worker_stale_since.items())
                    if (now - since) > self.eviction_grace_period
                ]
                for name in evicted:
                    self._worker_stale_since.pop(name, None)
                    logger.warning(
                        f"Worker {name} evicted (stale for >{self.eviction_grace_period}s)",
                    )
                    self._disconnect_worker(name, reason="evicted")
                    self._unclaim_worker_executions(name)

                await self._reclaim_orphaned_executions()

                expired = [
                    name
                    for name, since in self._worker_offline_since.items()
                    if (now - since) > self.offline_ttl
                ]
                for name in expired:
                    self._worker_offline_since.pop(name, None)
                    self._worker_cache.pop(name, None)
                    # Credential GC: a pruned worker's principal would otherwise
                    # accumulate forever (one service-account row per unique
                    # worker name). Disable it and revoke its keys; a genuine
                    # comeback re-registers and re-enables the same principal.
                    self._gc_worker_principal(name)
                    logger.debug(f"Pruned offline worker {name} (exceeded {self.offline_ttl}s TTL)")
        except asyncio.CancelledError:
            logger.info("Heartbeat reaper stopped")

    @staticmethod
    def _validate_security_config(settings) -> None:
        """Fail fast on security misconfiguration instead of erroring mid-traffic.

        With auth enabled, a missing execution-token secret used to surface only
        when the first worker token was minted, and a missing encryption key when
        the first secret was stored. Debug mode is exempt (execution-token secrets
        are auto-generated ephemerally there).
        """
        security = settings.security
        if security.auth.enabled and not settings.debug:
            problems = []
            if not security.execution_token_secret:
                problems.append(
                    "[flux.security] execution_token_secret is required when auth is "
                    "enabled (FLUX_SECURITY__EXECUTION_TOKEN_SECRET)",
                )
            if not security.encryption.encryption_key:
                problems.append(
                    "[flux.security.encryption] encryption_key is required when auth "
                    "is enabled: without it the secrets store is unusable and at-rest "
                    "pickle payloads are unsigned "
                    "(FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY)",
                )
            if problems:
                raise RuntimeError(
                    "Refusing to start with incomplete security configuration:\n- "
                    + "\n- ".join(problems),
                )
        elif not security.encryption.encryption_key and not settings.debug:
            logger.critical(
                "No encryption key configured: the secrets store is unavailable and "
                "at-rest pickle integrity signing is DISABLED. Set "
                "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY before production use.",
            )

    def _create_api(self) -> FastAPI:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Resolve / generate the bootstrap token here (not at module
            # construction) so merely creating the FastAPI app for tests or
            # OpenAPI generation does not have the side effect of creating
            # <home>/bootstrap-token. FastAPI guarantees the startup half of
            # lifespan runs before any request is dispatched.
            from flux.security.bootstrap_token import resolve_or_generate

            startup_settings = Configuration.get().settings
            self._validate_security_config(startup_settings)
            token, _ = resolve_or_generate(
                home=startup_settings.home,
                configured=startup_settings.workers.bootstrap_token,
            )
            self._bootstrap_token = token

            # First-admin bootstrap (issue #154): with auth enabled and no
            # admin principal yet, seed one with a random API key delivered
            # via a host-local 0600 file — never over the network. Runs after
            # seed_built_in_roles (the app was constructed before lifespan
            # fires), so the admin role row exists. Init-only: a no-op
            # whenever any enabled admin principal already exists.
            # Requires the API-key provider: in an OIDC-only deployment the
            # minted key could never authenticate, so nothing is seeded —
            # the first admin there is an OIDC principal granted the admin
            # role instead.
            if (
                startup_settings.security.auth.enabled
                and startup_settings.security.auth.api_keys.enabled
            ):
                from flux.security import admin_bootstrap

                try:
                    await admin_bootstrap.ensure_admin_key(
                        auth_service,
                        principal_registry,
                        self._get_db_session,
                        startup_settings.home,
                    )
                except Exception:
                    logger.error("Admin-key bootstrap failed", exc_info=True)

            # All blocking DB work reaches the loop through asyncio.to_thread,
            # which uses the loop's default executor — normally capped at
            # min(32, cpu+4) threads, an invisible throughput ceiling shared
            # with everything else. Install an explicitly sized executor so DB
            # concurrency is a deliberate knob paired with the connection pool.
            db_executor = None
            executor_threads = startup_settings.database_executor_threads
            if executor_threads > 0:
                from concurrent.futures import ThreadPoolExecutor

                db_executor = ThreadPoolExecutor(
                    max_workers=executor_threads,
                    thread_name_prefix="flux-db",
                )
                asyncio.get_running_loop().set_default_executor(db_executor)

            yield
            if self._scheduler_loop_obj is not None:
                await self._scheduler_loop_obj.stop()
            await self._stop_reaper()
            if self._dispatcher is not None:
                await self._dispatcher.stop()
                self._dispatcher = None
            if self._retention_job is not None:
                await self._retention_job.stop()
                self._retention_job = None
            if db_executor is not None:
                db_executor.shutdown(wait=False, cancel_futures=True)

            from flux.observability import shutdown as shutdown_observability

            shutdown_observability()

        api = FastAPI(
            title="Flux",
            version=self._get_version(),
            docs_url="/docs",
            lifespan=lifespan,
        )

        # Global request-body cap (SEC5): checkpoint/run-input/progress bodies
        # are dill payloads read into memory; without this a single request
        # can exhaust the server. Added FIRST so it is the INNERMOST layer:
        # its streamed-body 413 is raised from receive() during body parsing,
        # and a BaseHTTPMiddleware (SlowAPI, the anonymous-policy hook) in
        # between would launder that HTTPException into a generic 400 through
        # its receive bridge.
        from flux.api.body_limit import BodySizeLimitMiddleware

        _max_body = Configuration.get().settings.server_max_body_size
        if _max_body > 0:
            api.add_middleware(BodySizeLimitMiddleware, max_body_size=_max_body)

        limiter = Limiter(key_func=get_remote_address)
        api.state.limiter = limiter
        api.add_middleware(SlowAPIMiddleware)
        api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

        _cors_origins = Configuration.get().settings.cors_allow_origins
        _cors_credentials = Configuration.get().settings.cors_allow_credentials
        # Browsers reject `Access-Control-Allow-Origin: *` together with
        # credentials, and the combination is a CSRF footgun — force credentials
        # off whenever origins are wildcarded.
        if "*" in _cors_origins:
            _cors_credentials = False
        api.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_credentials=_cors_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        _MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

        # GET routes that change state, which the verb alone does not reveal.
        # A new one must be added here or it is anonymously reachable.
        _MUTATING_GETS = (
            re.compile(r"^/workflows/[^/]+/[^/]+/cancel/[^/]+/?$"),
            re.compile(r"^/workers/[^/]+/connect/?$"),
        )

        def _is_state_changing(method: str, path: str) -> bool:
            if method in _MUTATING_METHODS:
                return True
            return method in ("GET", "HEAD") and any(p.match(path) for p in _MUTATING_GETS)

        @api.middleware("http")
        async def _enforce_anonymous_policy(request: Request, call_next):
            # Secure default: when authentication is disabled, refuse anonymous
            # state-changing requests unless the operator has explicitly accepted
            # anonymous access. Read-only requests (and all requests when auth is
            # enabled) are unaffected; per-route checks still apply in that case.
            auth = Configuration.get().settings.security.auth
            if (
                not auth.enabled
                and not auth.allow_anonymous
                and _is_state_changing(request.method, request.url.path)
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": (
                            "Anonymous state-changing requests are disabled. Enable "
                            "authentication, or set "
                            "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS=true to explicitly "
                            "permit anonymous access."
                        ),
                    },
                )
            return await call_next(request)

        auth_config = Configuration.get().settings.security.auth
        from flux.security.principals import PrincipalRegistry

        principal_registry = PrincipalRegistry(session_factory=self._get_db_session)
        auth_service = AuthService(
            config=auth_config,
            session_factory=self._get_db_session,
            registry=principal_registry,
        )
        auth_service.seed_built_in_roles()
        init_auth_service(auth_service)

        if not auth_config.enabled and not Configuration.get().settings.debug:
            logger.critical(
                "Authentication is DISABLED. All requests are treated as the ANONYMOUS "
                "admin principal. This is not safe for production. Enable an auth "
                "provider via [flux.security.auth.oidc] or [flux.security.auth.api_keys] "
                "before exposing this server.",
            )

        from flux.observability import get_metrics, is_enabled

        if is_enabled():
            from flux.observability.middleware import MetricsMiddleware

            metrics = get_metrics()
            if metrics:
                api.add_middleware(MetricsMiddleware, metrics=metrics)

        self._register_system_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_workflow_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_worker_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_admin_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_schedule_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_hook_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_execution_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_dynamic_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_service_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_rbac_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_auth_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        return api


if __name__ == "__main__":  # pragma: no cover
    settings = Configuration.get().settings
    Server(settings.server_host, settings.server_port).start()
