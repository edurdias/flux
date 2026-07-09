from __future__ import annotations

import asyncio
import platform
import random
import signal
import time
from collections.abc import Awaitable
from collections.abc import Callable

import httpx
import psutil
from httpx_sse import aconnect_sse
from pydantic import BaseModel

from flux import ExecutionContext
from flux.config import Configuration
from flux.domain.events import ExecutionEvent
from flux.errors import (
    RunnerNotAvailableError,
    StaleClaimError,
    WorkerProcessCrashed,
)
from flux.runners import create_runners
from flux.runners.base import RunnerHooks
from flux.utils import get_logger

logger = get_logger(__name__)


class WorkflowDefinition(BaseModel):
    id: str
    namespace: str = "default"
    name: str
    version: int
    source: str


class WorkflowExecutionRequest(BaseModel):
    workflow: WorkflowDefinition
    context: ExecutionContext
    exec_token: str | None = None
    # Runner the workflow requires (from the dispatch payload); None means
    # the worker's configured default_runner.
    runner: str | None = None

    class Config:
        arbitrary_types_allowed = True

    @staticmethod
    def from_json(
        data: dict,
        checkpoint: Callable[[ExecutionContext], Awaitable],
    ) -> WorkflowExecutionRequest:
        exec_token = data.get("exec_token")
        ctx: ExecutionContext = ExecutionContext(
            workflow_id=data["context"]["workflow_id"],
            workflow_namespace=data["context"].get("workflow_namespace", "default"),
            workflow_name=data["context"]["workflow_name"],
            input=data["context"]["input"],
            execution_id=data["context"]["execution_id"],
            state=data["context"]["state"],
            events=[ExecutionEvent(**event) for event in data["context"]["events"]],
            checkpoint=checkpoint,
        )
        if exec_token:
            ctx.set_exec_token(exec_token)
        return WorkflowExecutionRequest(
            workflow=WorkflowDefinition(**data["workflow"]),
            context=ctx,
            exec_token=exec_token,
            runner=data.get("runner"),
        )


class _CheckpointOutbox:
    """Per-execution checkpoint sender state.

    Checkpoints are cumulative full-context snapshots, so the newest snapshot
    supersedes any unsent older one — coalescing to ``latest`` is lossless.
    A single sender task per execution serializes sends; in-flight sends are
    never cancelled, and failures are retried with capped backoff until the
    snapshot is delivered or superseded.
    """

    def __init__(self):
        self.latest: ExecutionContext | None = None
        self.generation = 0  # bumped on every new snapshot
        self.acked = 0  # highest generation successfully sent
        self.wakeup = asyncio.Event()
        self.sender: asyncio.Task | None = None
        self.delivered = asyncio.Event()  # set once a terminal snapshot is acked
        self.closed = False  # handler ended; sender exits after catching up
        # Event ids already acknowledged by the server. Snapshots are
        # cumulative, so each send carries only events NOT in this set —
        # O(delta) payloads instead of O(history) per checkpoint.
        self.acked_ids: set[str] = set()


class Worker:
    def __init__(self, name: str, server_url: str, labels: dict[str, str] | None = None):
        self.name = name
        self.labels = labels or {}
        config = Configuration.get().settings.workers
        # Normalize so a whitespace-only token (e.g. an empty-after-strip env var)
        # surfaces as "not configured" instead of being sent as `Bearer    ` and
        # producing a confusing 403 from the server.
        bootstrap_token = (
            config.bootstrap_token.strip() if config.bootstrap_token is not None else None
        )
        if not bootstrap_token:
            raise RuntimeError(
                "Worker bootstrap token is not configured. Set FLUX_WORKERS__BOOTSTRAP_TOKEN "
                "or 'bootstrap_token' under [flux.workers] in flux.toml. Retrieve the server's "
                "token by running 'flux server bootstrap-token' on the server host.",
            )
        self.bootstrap_token = bootstrap_token
        self.base_url = f"{server_url or config.server_url}/workers"
        self.client = httpx.AsyncClient(timeout=config.http_timeout or None)
        self._running_workflows: dict[str, asyncio.Task] = {}
        self._checkpoint_outboxes: dict[str, _CheckpointOutbox] = {}
        self._claim_generations: dict[str, str] = {}
        # Transient executions whose RUNNING transition has been persisted.
        self._transient_started: set[str] = set()
        self._checkpoint_retry_max_delay: float = config.checkpoint_retry_max_delay
        self._terminal_checkpoint_deadline: float = config.terminal_checkpoint_deadline
        self._reauth_lock = asyncio.Lock()
        self._progress_queues: dict[str, asyncio.Queue] = {}
        self._progress_flushers: dict[str, asyncio.Task | None] = {}
        self._reconnect_max_delay = config.reconnect_max_delay
        self._runners = create_runners(list(config.runners), config)
        self._default_runner = config.default_runner
        if self._default_runner not in self._runners:
            raise ValueError(
                f"[flux.workers] default_runner '{self._default_runner}' is not "
                f"among the enabled runners {sorted(self._runners)}",
            )
        self._max_concurrent = config.max_concurrent_executions
        self._drain_timeout = config.drain_timeout
        self._draining = False
        # Self-health: an event-loop starved by misbehaving in-process code
        # makes the worker a black hole (accepts dispatches it can't run).
        # The lag monitor flips this off after consecutive breaches; while
        # unhealthy the worker declines/releases new work and advertises the
        # state on heartbeats so dispatch routes around it.
        self._healthy = True
        self._loop_lag_threshold = config.loop_lag_threshold
        self._loop_lag_probe_interval = config.loop_lag_probe_interval
        # Metrics provider: user callable (sync or async) returning
        # dict[str, float], advertised on heartbeat pongs for routing
        # policies ("metric:*" selectors). Collected lazily on pong at
        # metrics_interval cadence; a broken provider disables itself.
        self._metrics_provider = self._load_metrics_provider(config.metrics_provider)
        self._metrics_interval = config.metrics_interval
        self._metrics_snapshot: dict[str, float] | None = None
        self._metrics_collected_at: float | None = None
        self._user_metrics: dict[str, float] | None = None
        # Built-in flux.* metrics: loop lag, throughput/failure aggregates,
        # system gauges — published without a provider so policies can rank
        # on them out of the box.
        self._metrics_collector = None
        if config.builtin_metrics:
            from flux.worker_metrics import WorkerMetricsCollector

            inproc = self._runners.get("inprocess")
            self._metrics_collector = WorkerMetricsCollector(
                max_concurrent=config.max_concurrent_executions or None,
                warm_modules=getattr(inproc, "warm_modules", None),
            )
        # Non-transient executions awaiting their first checkpoint; feeds the
        # flux.startup_overhead_seconds built-in.
        self._execution_started: dict[str, float] = {}
        self._registered = False
        self.session_token: str | None = None

    @staticmethod
    def _load_metrics_provider(spec: str | None):
        """Resolve '[flux.workers] metrics_provider' ("pkg.module:callable")."""
        if not spec:
            return None
        try:
            module_path, _, attr = spec.partition(":")
            if not module_path or not attr:
                raise ValueError("expected format 'package.module:callable'")
            import importlib

            provider = getattr(importlib.import_module(module_path), attr)
            if not callable(provider):
                raise ValueError(f"'{spec}' is not callable")
            return provider
        except Exception as e:
            logger.warning(f"Metrics provider '{spec}' could not be loaded ({e}); disabled")
            return None

    async def _collect_metrics(self) -> dict[str, float] | None:
        """Latest validated metrics snapshot, refreshed at metrics_interval.

        Sync providers run in a thread so a slow collector cannot starve the
        event loop; any failure keeps the previous snapshot.
        """
        if self._metrics_provider is None and self._metrics_collector is None:
            return None
        now = time.monotonic()
        if (
            self._metrics_collected_at is not None
            and now - self._metrics_collected_at < self._metrics_interval
        ):
            return self._metrics_snapshot
        self._metrics_collected_at = now

        if self._metrics_provider is not None:
            try:
                from flux.routing import RESERVED_METRIC_PREFIX, validate_worker_metrics
                from flux.utils import maybe_awaitable

                # Bounded so a hung provider cannot block pongs, floored so
                # a sub-second refresh cadence never strangles collection
                # itself (thread dispatch alone can exceed a tiny interval).
                async with asyncio.timeout(min(5.0, max(1.0, self._metrics_interval))):
                    if asyncio.iscoroutinefunction(self._metrics_provider):
                        raw = await self._metrics_provider()
                    else:
                        raw = await asyncio.to_thread(self._metrics_provider)
                    raw = await maybe_awaitable(raw)
                validated = validate_worker_metrics(raw)
                if validated is None:
                    logger.warning(
                        f"Metrics provider returned an invalid payload ({raw!r}); "
                        "keeping the previous snapshot",
                    )
                else:
                    stripped = {
                        key: value
                        for key, value in validated.items()
                        if not key.startswith(RESERVED_METRIC_PREFIX)
                    }
                    if len(stripped) != len(validated):
                        logger.debug(
                            "Metrics provider keys under the reserved "
                            f"'{RESERVED_METRIC_PREFIX}' prefix were dropped",
                        )
                    self._user_metrics = stripped
            except Exception as e:
                logger.warning(f"Metrics provider failed ({e}); keeping the previous snapshot")

        merged = dict(self._user_metrics or {})
        if self._metrics_collector:
            # Built-ins win: user values can never impersonate a flux.* signal.
            merged.update(self._metrics_collector.snapshot(len(self._running_workflows)))
        self._metrics_snapshot = merged or None
        return self._metrics_snapshot

    def start(self):
        logger.info("Worker starting up...")
        logger.debug(f"Worker name: {self.name}")
        logger.debug(f"Server URL: {self.base_url}")

        obs_config = Configuration.get().settings.observability
        if obs_config.enabled:
            from flux.observability import setup as setup_observability

            setup_observability(obs_config)
            logger.info("Worker observability initialized")

        try:
            asyncio.run(self._run())
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Worker shutdown requested")
        finally:
            try:
                from flux.observability import shutdown as shutdown_observability

                shutdown_observability()
            except Exception:
                pass
            logger.info("Worker shutting down...")

    async def _run(self):
        """Main worker loop: register, connect, reconnect on failure.

        On first run, performs full registration (gathers runtime, resources, packages).
        On reconnect, skips registration and reuses the existing session token.
        If the server rejects the token (401/403), falls back to full re-registration.
        """
        # systemd/Docker/k8s stop containers with SIGTERM, not SIGINT. Register
        # cooperative loop-level handlers so a stop signal cancels the worker at
        # an await boundary — letting in-flight checkpoints and shutdown
        # finally-blocks run — instead of raising KeyboardInterrupt at an
        # arbitrary bytecode point mid-coroutine.
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()

        def _request_shutdown(signame: str) -> None:
            if self._draining:
                logger.info(f"Worker received {signame} again, aborting drain...")
            else:
                logger.info(f"Worker received {signame}, draining before shutdown...")
            if main_task is not None:
                main_task.cancel()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_shutdown, sig.name)
            except (NotImplementedError, RuntimeError, ValueError):
                # Unsupported on Windows / outside the main thread; the default
                # SIGINT->KeyboardInterrupt behaviour still applies there.
                logger.debug(f"Could not install {sig.name} handler via event loop")

        health_monitor: asyncio.Task | None = None
        if self._loop_lag_threshold > 0:
            health_monitor = asyncio.create_task(self._monitor_loop_health())

        backoff = 1
        try:
            while True:
                try:
                    if not self._registered:
                        await self._register()
                    else:
                        logger.info("Reconnecting with existing session...")

                    try:
                        await self._connect()
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code in (401, 403) and self._registered:
                            logger.warning("Session token rejected, re-registering...")
                            self._registered = False
                            await self._register()
                            await self._connect()
                        else:
                            raise

                    logger.info("SSE connection closed, reconnecting...")
                    backoff = 1
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    jitter = backoff * (0.5 + random.random())
                    delay = min(jitter, self._reconnect_max_delay)
                    logger.warning(
                        f"Connection lost ({type(e).__name__}: {e}). Reconnecting in {delay:.1f}s...",
                    )
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, self._reconnect_max_delay)
        except asyncio.CancelledError:
            # First stop signal: the SSE stream is closed (no new work arrives),
            # so drain what is already running. A second signal cancels again,
            # which interrupts the drain's awaits and exits immediately.
            self._draining = True
            if health_monitor is not None:
                health_monitor.cancel()
                import contextlib

                with contextlib.suppress(asyncio.CancelledError):
                    await health_monitor
            await self._drain()

    async def _monitor_loop_health(self):
        """Detect event-loop starvation and step out of the dispatch pool.

        Measures scheduling lag (actual vs requested sleep). Three
        consecutive probes over ``loop_lag_threshold`` flip the worker
        unhealthy: new dispatches are released back for re-dispatch and
        heartbeat pongs advertise the state so the server routes around this
        worker. Running executions are left to finish. Three consecutive
        clean probes recover. Note the probe itself runs on the starved loop
        — under total starvation it can't fire at all, and the server-side
        heartbeat reaper remains the backstop.
        """
        breaches = 0
        recoveries = 0
        while True:
            start = time.monotonic()
            await asyncio.sleep(self._loop_lag_probe_interval)
            lag = time.monotonic() - start - self._loop_lag_probe_interval

            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_loop_lag(lag)
            if self._metrics_collector:
                self._metrics_collector.record_loop_lag(lag)

            if lag >= self._loop_lag_threshold:
                recoveries = 0
                breaches += 1
                if breaches >= 3 and self._healthy:
                    self._healthy = False
                    logger.error(
                        f"Event loop starved (lag {lag:.2f}s >= "
                        f"{self._loop_lag_threshold}s for {breaches} probes); "
                        f"marking worker unhealthy — declining new work until "
                        f"the loop recovers",
                    )
                    if m:
                        m.record_worker_health_transition("unhealthy")
                    # Tell the server immediately instead of waiting for the
                    # next ping/pong round-trip.
                    asyncio.create_task(self._send_pong())
            else:
                breaches = 0
                if not self._healthy:
                    recoveries += 1
                    if recoveries >= 3:
                        self._healthy = True
                        recoveries = 0
                        logger.warning(
                            "Event loop recovered; worker healthy — accepting work again",
                        )
                        if m:
                            m.record_worker_health_transition("recovered")
                        asyncio.create_task(self._send_pong())

    async def _drain(self):
        """Let running executions finish, then flush their checkpoints.

        Bounded by ``drain_timeout``: executions still running at the deadline
        are cancelled (which checkpoints them as CANCELLED, best-effort), and
        outstanding checkpoint senders get a short final window so terminal
        states reach the server instead of waiting on the ~60s reaper path.
        """
        running = [t for t in self._running_workflows.values() if not t.done()]
        if running:
            if self._drain_timeout > 0:
                logger.info(
                    f"Draining {len(running)} running execution(s) "
                    f"(timeout {self._drain_timeout}s)...",
                )
                _, pending = await asyncio.wait(running, timeout=self._drain_timeout)
            else:
                pending = set(running)
            if pending:
                logger.warning(
                    f"Drain deadline reached; cancelling {len(pending)} execution(s)",
                )
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=30)

        senders = [
            box.sender
            for box in self._checkpoint_outboxes.values()
            if box.sender and not box.sender.done()
        ]
        if senders:
            logger.info(f"Flushing {len(senders)} outstanding checkpoint sender(s)...")
            await asyncio.wait(senders, timeout=30)
        logger.info("Drain complete")

    async def _register(self):
        try:
            logger.info(f"Registering worker '{self.name}' with server...")
            logger.debug(f"Registration endpoint: {self.base_url}/register")

            runtime = await self._get_runtime_info()
            resources = await self._get_resources_info()
            packages = await self._get_installed_packages()

            logger.debug(f"Runtime info: {runtime}")
            logger.debug(
                f"Resource info: CPU: {resources['cpu_total']}, Memory: {resources['memory_total']}, Disk: {resources['disk_total']}",
            )
            logger.debug(f"Number of packages to register: {len(packages)}")

            registration = {
                "name": self.name,
                "runtime": runtime,
                "resources": resources,
                "packages": packages,
                "labels": self.labels,
                "max_concurrent_executions": self._max_concurrent or None,
                "runners": sorted(self._runners),
            }

            logger.debug("Sending registration request to server...")
            response = await self.client.post(
                f"{self.base_url}/register",
                json=registration,
                headers={"Authorization": f"Bearer {self.bootstrap_token}"},
            )
            response.raise_for_status()
            data = response.json()
            self.session_token = data["session_token"]
            self._registered = True
            logger.debug("Registration successful, received session token")
            logger.info("OK")
        except Exception as e:
            logger.error("ERROR")
            logger.exception(e)
            raise

    async def _connect(self):
        """Connect to SSE endpoint and handle events asynchronously"""
        logger.info("Establishing connection with server...")

        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}

        logger.debug(f"SSE connection URL: {base_url}/connect")
        logger.debug("Setting up HTTP client for long-running connection")

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                logger.debug("Initiating SSE connection...")
                async with aconnect_sse(
                    client,
                    "GET",
                    f"{base_url}/connect",
                    headers=headers,
                ) as es:
                    logger.info("Connection established successfully")
                    logger.debug("Starting event loop to receive events")
                    async for evt in es.aiter_sse():
                        if evt.event == "execution_scheduled":
                            asyncio.create_task(
                                self._handle_execution_scheduled(base_url, evt),
                                name=f"handle_execution_scheduled_{evt.id}",
                            )
                        elif evt.event == "execution_cancelled":
                            asyncio.create_task(
                                self._handle_execution_cancelled(evt),
                                name=f"handle_execution_cancelled_{evt.id}",
                            )
                        elif evt.event == "execution_resumed":
                            asyncio.create_task(
                                self._handle_execution_resumed(evt),
                                name=f"handle_execution_resumed_{evt.id}",
                            )
                        elif evt.event == "ping":
                            asyncio.create_task(self._send_pong())
                        elif evt.event == "keep-alive":
                            logger.debug("Event received: Keep-alive")
                        elif evt.event == "error":
                            logger.error(f"Event received: Error - {evt.data}")

        except Exception as evt:
            logger.error(f"Error in SSE connection: {str(evt)}")
            logger.debug(f"Connection error details: {type(evt).__name__}: {str(evt)}")
            raise

    async def _send_pong(self):
        """Respond to server ping with a pong carrying health and metrics."""
        base_url = f"{self.base_url}/{self.name}"
        try:
            payload: dict = {"healthy": self._healthy}
            metrics = await self._collect_metrics()
            if metrics is not None:
                payload["metrics"] = metrics
            await self._authorized_post(f"{base_url}/pong", json=payload)
            logger.debug("Pong sent")
        except Exception as e:
            logger.debug(f"Failed to send pong: {e}")

    async def _handle_execution_cancelled(self, e):
        """Handle execution cancelled event asynchronously.

        This method is called as a separate task and should handle its own exceptions.
        """
        try:
            logger.debug("Received execution_cancelled event")
            data = e.json()
            context = ExecutionContext.from_json(data["context"], self._checkpoint)
            logger.info(f"Cancelling Execution - {context.workflow_name} - {context.execution_id}")
            if task := self._running_workflows.get(context.execution_id):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"Execution {context.execution_id} cancelled successfully")
                    from flux.observability import get_metrics

                    m = get_metrics()
                    if m:
                        m.record_workflow_completed(
                            context.workflow_namespace,
                            context.workflow_name,
                            "cancelled",
                            0,
                        )
                finally:
                    self._running_workflows.pop(context.execution_id, None)
        except Exception as ex:
            logger.error(f"Error handling execution_cancelled event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)

    async def _handle_execution_resumed(self, e):
        """Handle execution resumed event asynchronously."""
        import contextlib

        from flux.observability import get_metrics, is_enabled

        event_data = e.json()
        request = WorkflowExecutionRequest.from_json(event_data, self._checkpoint)
        m = get_metrics()

        span_cm = contextlib.nullcontext()
        if is_enabled():
            from opentelemetry import trace as _trace

            from flux.observability.tracing import extract_trace_context

            trace_ctx = event_data.get("trace_context", {})
            parent_context = extract_trace_context(trace_ctx) if trace_ctx else None

            tracer = _trace.get_tracer("flux")
            span_cm = tracer.start_as_current_span(
                "flux.workflow.resume",
                context=parent_context,
                attributes={
                    "flux.workflow.name": request.workflow.name,
                    "flux.execution.id": request.context.execution_id,
                    "flux.worker.name": self.name,
                },
            )

        if not self._healthy:
            logger.warning(
                f"Unhealthy (event-loop lag); releasing resumed execution "
                f"{request.context.execution_id} for re-dispatch",
            )
            await self._release_claim(request.context.execution_id)
            return

        try:
            with span_cm as span:
                logger.info(
                    f"Resuming Execution - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )

                base_url = f"{self.base_url}/{self.name}"

                logger.debug(f"Claiming resumed execution: {request.context.execution_id}")
                try:
                    response = await self._authorized_post(
                        f"{base_url}/claim/{request.context.execution_id}",
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as claim_err:
                    if claim_err.response.status_code == 409:
                        logger.info(
                            f"Resume claim for {request.context.execution_id} returned 409 "
                            f"(already claimed); dropping duplicate dispatch.",
                        )
                        return
                    raise

                logger.info(
                    f"Execution Claimed - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )

                claim_data = response.json()
                generation = response.headers.get("X-Flux-Claim-Generation")
                if generation is not None:
                    self._claim_generations[request.context.execution_id] = generation
                request.context = ExecutionContext.from_json(claim_data, self._checkpoint)
                if request.exec_token:
                    request.context.set_exec_token(request.exec_token)

                if m:
                    m.record_worker_execution_started(self.name)

                try:
                    ctx = await self._execute_workflow(request)
                finally:
                    if m:
                        m.record_worker_execution_ended(self.name)

                if ctx.has_failed:
                    if span:
                        from opentelemetry.trace import StatusCode

                        span.set_status(StatusCode.ERROR, str(ctx.output))
                    logger.error(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
                else:
                    logger.info(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
        except Exception as ex:
            logger.error(f"Error handling execution_resumed event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)
        finally:
            self._close_checkpoint_outbox(request.context.execution_id)

    async def _handle_execution_scheduled(self, base_url, e):
        """Handle execution scheduled event asynchronously.

        This method is called as a separate task and should handle its own exceptions.
        """
        import contextlib

        from flux.observability import get_metrics, is_enabled

        event_data = e.json()
        request = WorkflowExecutionRequest.from_json(event_data, self._checkpoint)
        m = get_metrics()

        span_cm = contextlib.nullcontext()
        if is_enabled():
            from opentelemetry import trace as _trace

            from flux.observability.tracing import extract_trace_context

            trace_ctx = event_data.get("trace_context", {})
            parent_context = extract_trace_context(trace_ctx) if trace_ctx else None

            tracer = _trace.get_tracer("flux")
            span_cm = tracer.start_as_current_span(
                "flux.workflow.execute",
                context=parent_context,
                attributes={
                    "flux.workflow.name": request.workflow.name,
                    "flux.execution.id": request.context.execution_id,
                    "flux.worker.name": self.name,
                },
            )

        if self._draining:
            logger.info(
                f"Draining; not claiming execution {request.context.execution_id} "
                f"(will be re-dispatched)",
            )
            return

        if not self._healthy:
            # Unlike draining (where disconnect drains the queue), an
            # unhealthy worker stays connected — release the assignment
            # explicitly so it re-dispatches now instead of idling on us.
            logger.warning(
                f"Unhealthy (event-loop lag); releasing execution "
                f"{request.context.execution_id} for re-dispatch",
            )
            await self._release_claim(request.context.execution_id)
            return

        is_transient = bool(event_data.get("transient"))
        try:
            with span_cm as span:
                logger.info(
                    f"Execution Scheduled - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )
                logger.debug(f"Workflow input: {request.context.input}")

                logger.debug(f"Claiming execution: {request.context.execution_id}")
                response = await self._authorized_post(
                    f"{base_url}/claim/{request.context.execution_id}",
                )
                response.raise_for_status()
                claim_data = response.json()
                generation = response.headers.get("X-Flux-Claim-Generation")
                if generation is not None:
                    self._claim_generations[request.context.execution_id] = generation
                request.context = ExecutionContext.from_json(claim_data, self._checkpoint)
                if is_transient:
                    request.context.mark_transient()
                if request.exec_token:
                    request.context.set_exec_token(request.exec_token)
                logger.debug(f"Claim response: {claim_data}")

                logger.info(
                    f"Execution Claimed - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )

                if m:
                    m.record_worker_execution_started(self.name)

                try:
                    logger.debug(f"Starting workflow execution: {request.workflow.name}")
                    ctx = await self._execute_workflow(request)
                    logger.debug(
                        f"Workflow execution completed with state: {ctx.state.value}",
                    )
                finally:
                    if m:
                        m.record_worker_execution_ended(self.name)

                if ctx.has_failed:
                    if span:
                        from opentelemetry.trace import StatusCode

                        span.set_status(StatusCode.ERROR, str(ctx.output))
                    logger.error(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
                    logger.debug(
                        f"Failure details: {ctx.events[-1].value if ctx.events else 'No details'}",
                    )
                else:
                    logger.info(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
                    logger.debug(f"Execution output: {ctx.output}")
        except Exception as ex:
            logger.error(f"Error handling execution_scheduled event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)
        finally:
            self._close_checkpoint_outbox(request.context.execution_id)

    async def _execute_workflow(self, request: WorkflowExecutionRequest) -> ExecutionContext:
        """Execute a workflow from a workflow execution request.

        Args:
            request: The workflow execution request containing the workflow definition and context

        Returns:
            ExecutionContext: The execution context after workflow execution
        """
        from flux.remote_managers import (
            RemoteApprovalStore,
            RemoteConfigManager,
            RemoteSecretManager,
            reset_remote_managers,
            set_remote_managers,
        )

        server_url = self.base_url.rsplit("/workers", 1)[0]
        config_manager = RemoteConfigManager(server_url, self.session_token)
        secret_manager = RemoteSecretManager(
            server_url,
            self.session_token,
            worker_name=self.name,
            execution_id=request.context.execution_id,
        )
        approval_store = RemoteApprovalStore(
            server_url,
            self.session_token,
            worker_name=self.name,
        )
        remote_tokens = set_remote_managers(
            config=config_manager,
            secret=secret_manager,
            approvals=approval_store,
        )
        hooks = RunnerHooks(
            checkpoint=self._checkpoint,
            get_secrets=secret_manager.get,
            get_configs=config_manager.get,
            progress=self._record_progress,
            get_approval=self._approval_get,
            register_approval=self._approval_register,
        )
        try:
            return await self._run_workflow(request, hooks)
        finally:
            reset_remote_managers(remote_tokens)
            await config_manager.aclose()
            await secret_manager.aclose()
            await approval_store.aclose()

    async def _run_workflow(
        self,
        request: WorkflowExecutionRequest,
        hooks: RunnerHooks,
    ) -> ExecutionContext:
        logger.debug(
            f"Preparing to execute workflow: {request.workflow.name} v{request.workflow.version}",
        )

        runner_name = request.runner or self._default_runner
        runner = self._runners.get(runner_name)
        if runner is None:
            # Dispatch matching filters on advertised runners, so this only
            # happens on races (config change between register and dispatch)
            # or workers registered before runner capabilities existed.
            raise RunnerNotAvailableError(runner_name, sorted(self._runners))

        ctx = request.context
        self._setup_progress(ctx)

        logger.debug(f"Executing workflow {request.workflow.name} via runner '{runner_name}'")
        task = asyncio.create_task(runner.execute(request, hooks))
        self._running_workflows[ctx.execution_id] = task
        start_time = asyncio.get_event_loop().time()
        if self._metrics_collector and not ctx.is_transient:
            # Resolved by the first checkpoint -> flux.startup_overhead_seconds.
            self._execution_started[ctx.execution_id] = time.monotonic()
        try:
            ctx = await task
        except WorkerProcessCrashed as crash:
            return await self._handle_runner_crash(request, crash)
        finally:
            self._running_workflows.pop(request.context.execution_id, None)
            self._execution_started.pop(request.context.execution_id, None)
            await self._teardown_progress(request.context.execution_id)
            logger.debug(f"Workflow execution async task removed: {request.workflow.name}")

        execution_time = asyncio.get_event_loop().time() - start_time
        logger.debug(f"Workflow execution completed in {execution_time:.4f}s")

        if self._metrics_collector:
            self._metrics_collector.record_duration(execution_time)
            self._metrics_collector.record_outcome(
                "failed" if ctx.has_failed else "completed",
            )

        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            status = "completed" if not ctx.has_failed else "failed"
            m.record_workflow_completed(
                request.workflow.namespace,
                request.workflow.name,
                status,
                execution_time,
            )

        return ctx

    async def _handle_runner_crash(
        self,
        request: WorkflowExecutionRequest,
        crash: WorkerProcessCrashed,
    ) -> ExecutionContext:
        """Map a dead runner child to the execution's durability semantics.

        Durable executions are released back to the server for re-dispatch:
        every checkpoint the child delivered before dying is persisted, so
        deterministic replay resumes from the last saved task and completed
        tasks are not re-run. Transient executions are at-most-once by
        decision — they fail terminally and the caller retries.
        """
        ctx = crash.last_context or request.context
        logger.error(str(crash))
        if self._metrics_collector:
            self._metrics_collector.record_outcome("crashed")
        if ctx.is_transient:
            ctx.fail(
                ctx.execution_id,
                {"type": "WorkerProcessCrashed", "message": str(crash)},
            )
            await self._checkpoint(ctx)
            return ctx
        await self._release_claim(ctx.execution_id)
        return ctx

    async def _release_claim(self, execution_id: str):
        """Hand an assigned execution back to the server for re-dispatch.

        Used when this worker cannot run work it was given: a crashed
        durable runner child, or a dispatch that arrived while unhealthy.
        Fenced by claim generation like checkpoints: if the server says the
        claim is stale, another worker already owns the execution and there
        is nothing to release.
        """
        # Flush pending checkpoints first: they carry the task completions
        # replay resumes from, and after release the bumped claim generation
        # would fence them.
        box = self._checkpoint_outboxes.get(execution_id)
        if box is not None:
            box.closed = True
            box.wakeup.set()
            if box.sender and not box.sender.done():
                try:
                    await asyncio.wait_for(asyncio.shield(box.sender), timeout=30)
                except TimeoutError:
                    logger.warning(
                        f"Timed out flushing checkpoints for crashed execution "
                        f"{execution_id}; releasing anyway",
                    )

        base_url = f"{self.base_url}/{self.name}"
        headers = {}
        generation = self._claim_generations.get(execution_id)
        if generation is not None:
            headers["X-Flux-Claim-Generation"] = generation

        backoff = 1.0
        for attempt in range(5):
            try:
                response = await self._authorized_post(
                    f"{base_url}/release/{execution_id}",
                    headers=headers,
                )
                if response.status_code == 409 and "stale-claim" in response.text:
                    logger.info(
                        f"Release of {execution_id} fenced (stale claim); another worker owns it",
                    )
                    break
                response.raise_for_status()
                logger.info(f"Execution {execution_id} released for re-dispatch")
                break
            except Exception as e:
                if attempt == 4:
                    logger.critical(
                        f"Could not release execution {execution_id} "
                        f"({type(e).__name__}: {e}); the server reaper will "
                        f"recover it when this worker's claim goes stale",
                    )
                    break
                delay = backoff * (0.5 + random.random())
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, 10.0)
        self._close_checkpoint_outbox(execution_id)
        self._claim_generations.pop(execution_id, None)
        self._transient_started.discard(execution_id)

    async def _checkpoint(self, ctx: ExecutionContext):
        """Queue a checkpoint for delivery, never dropping durability.

        Intermediate checkpoints return immediately: the per-execution sender
        task delivers the newest snapshot, retrying failures with capped
        backoff instead of cancelling in-flight sends. Terminal checkpoints
        block until the finished state is acknowledged by the server (or the
        configured deadline expires, after which the server reaper is the
        fallback).
        """
        started = self._execution_started.pop(ctx.execution_id, None)
        if (
            started is not None
            and self._metrics_collector
            and not ctx.is_transient
            and not ctx.has_finished
        ):
            # First intermediate checkpoint = user code is genuinely running;
            # the gap from dispatch is the runner's spawn/load overhead. A
            # terminal first checkpoint carries no startup signal (skipped).
            self._metrics_collector.record_startup(time.monotonic() - started)
        if ctx.is_transient:
            if ctx.is_paused:
                # Pause needs task-level history to replay on resume, which a
                # transient execution deliberately does not persist. Convert to
                # a terminal failure so nothing is silently stranded.
                from flux.errors import TransientDurabilityError

                error = TransientDurabilityError(ctx.execution_id, "pause")
                logger.warning(str(error))
                ctx.fail(
                    ctx.execution_id,
                    {"type": "TransientDurabilityError", "message": str(error)},
                )
            elif not ctx.has_finished:
                # The outer lifecycle persists like any regular run. The
                # RUNNING transition only ever travels inside a checkpoint (the
                # first task's, in durable mode), so let exactly one
                # intermediate checkpoint through — filtered to WORKFLOW_*
                # events by _send_checkpoint — and suppress the rest. The row
                # is observable and cancellable mid-flight; task progress
                # stays in memory until the terminal transition.
                if ctx.execution_id in self._transient_started:
                    return
                self._transient_started.add(ctx.execution_id)

        box = self._checkpoint_outboxes.get(ctx.execution_id)
        if box is None:
            box = self._checkpoint_outboxes[ctx.execution_id] = _CheckpointOutbox()

        box.latest = ctx
        box.generation += 1
        box.wakeup.set()

        if box.sender is None or box.sender.done():
            box.sender = asyncio.create_task(
                self._drain_checkpoints(ctx.execution_id, box),
            )

        if ctx.has_finished:
            try:
                await asyncio.wait_for(
                    box.delivered.wait(),
                    timeout=self._terminal_checkpoint_deadline or None,
                )
            except TimeoutError:
                logger.critical(
                    f"Terminal checkpoint for execution {ctx.execution_id} "
                    f"({ctx.workflow_name}, state={ctx.state.value}) not delivered within "
                    f"{self._terminal_checkpoint_deadline}s; giving up. The server reaper "
                    f"will requeue the execution.",
                )
                if box.sender and not box.sender.done():
                    box.sender.cancel()
                self._checkpoint_outboxes.pop(ctx.execution_id, None)

    async def _drain_checkpoints(self, execution_id: str, box: _CheckpointOutbox):
        """Deliver checkpoint snapshots for one execution, in order, until terminal.

        Runs as a dedicated task per execution: waits for new snapshots, sends
        the newest one, and retries failed sends with jittered capped backoff.
        Ends when a terminal snapshot is acknowledged.
        """
        initial_backoff = min(1.0, self._checkpoint_retry_max_delay or 1.0)
        backoff = initial_backoff
        while True:
            await box.wakeup.wait()
            box.wakeup.clear()

            while box.acked < box.generation:
                generation = box.generation
                ctx = box.latest
                assert ctx is not None
                try:
                    sent_ids = await self._send_checkpoint(
                        ctx,
                        exclude_event_ids=box.acked_ids,
                    )
                    box.acked_ids.update(sent_ids)
                    box.acked = generation
                    backoff = initial_backoff
                except asyncio.CancelledError:
                    raise
                except StaleClaimError:
                    self._abort_fenced_execution(execution_id, box)
                    return
                except Exception as e:
                    delay = backoff * (0.5 + random.random())
                    logger.warning(
                        f"Checkpoint for execution {execution_id} failed "
                        f"({type(e).__name__}: {e}); retrying in {delay:.1f}s",
                    )
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, self._checkpoint_retry_max_delay)

            latest = box.latest
            if latest is not None and latest.has_finished:
                box.delivered.set()
                self._checkpoint_outboxes.pop(execution_id, None)
                self._claim_generations.pop(execution_id, None)
                self._transient_started.discard(execution_id)
                return
            if box.closed:
                self._checkpoint_outboxes.pop(execution_id, None)
                self._claim_generations.pop(execution_id, None)
                self._transient_started.discard(execution_id)
                return

    def _abort_fenced_execution(self, execution_id: str, box: _CheckpointOutbox):
        """The server rejected our claim generation: another worker owns the
        execution now. Cancel the local copy and discard its checkpoint state
        — its writes must not interleave with the new owner's."""
        logger.warning(
            f"Execution {execution_id} was reassigned (stale claim); aborting the local copy",
        )
        task = self._running_workflows.get(execution_id)
        if task and not task.done():
            task.cancel()
        # Unblock any terminal-checkpoint waiter; the result is discarded
        # server-side anyway.
        box.delivered.set()
        self._checkpoint_outboxes.pop(execution_id, None)
        self._claim_generations.pop(execution_id, None)
        self._transient_started.discard(execution_id)

    def _close_checkpoint_outbox(self, execution_id: str):
        """Release checkpoint state once an execution's handler has ended.

        On normal completion the sender already removed the outbox after the
        terminal checkpoint was acknowledged, making this a no-op. For
        non-terminal endings (pause, handler error) the sender is asked to
        exit after delivering whatever is still pending — never cancelled, so
        an in-flight PAUSED or partial snapshot is not lost.
        """
        box = self._checkpoint_outboxes.get(execution_id)
        if box is None:
            return
        box.closed = True
        box.wakeup.set()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.session_token}"}

    async def _authorized_post(self, url: str, **kwargs) -> httpx.Response:
        """POST with the session token; on 401/403 re-register once and retry.

        Only the SSE connect path used to recover from token rejection, so a
        key rotated or revoked mid-execution silently broke checkpoints,
        claims, pongs, and progress. Persistent rejection after one refresh is
        returned to the caller (whose raise_for_status surfaces it).
        """
        token_used = self.session_token
        extra_headers = kwargs.pop("headers", None) or {}
        response = await self.client.post(
            url,
            headers={**self._auth_headers(), **extra_headers},
            **kwargs,
        )
        if response.status_code not in (401, 403) or not self._registered:
            return response

        async with self._reauth_lock:
            if self.session_token == token_used:
                logger.warning("Session token rejected mid-operation, re-registering...")
                self._registered = False
                await self._register()
        return await self.client.post(
            url,
            headers={**self._auth_headers(), **extra_headers},
            **kwargs,
        )

    async def _approval_get(self, execution_id: str, task_call_id: str) -> dict | None:
        """Approval-row lookup relayed for a runner child (RunnerHooks)."""
        response = await self.client.get(
            f"{self.base_url}/{self.name}/approvals/{execution_id}/{task_call_id}",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json().get("approval")

    async def _approval_register(self, execution_id: str, payload: dict) -> dict:
        """Approval-row registration relayed for a runner child (RunnerHooks)."""
        response = await self._authorized_post(
            f"{self.base_url}/{self.name}/approvals/{execution_id}",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def _send_checkpoint(
        self,
        ctx: ExecutionContext,
        exclude_event_ids: set[str] | None = None,
    ) -> list[str]:
        """POST one checkpoint; returns the ids of the events it carried.

        ``exclude_event_ids`` (the outbox's acknowledged set) turns the payload
        into a delta: snapshots are cumulative, so already-acknowledged events
        can be omitted — the server reconciles by event id either way. Raises
        ``StaleClaimError`` if the server fences the claim (409): the
        execution was reassigned and this worker's copy must abort.
        """
        base_url = f"{self.base_url}/{self.name}"
        try:
            logger.info(f"Checkpointing execution '{ctx.workflow_name}' ({ctx.execution_id})...")
            logger.debug(f"Checkpoint URL: {base_url}/checkpoint/{ctx.execution_id}")
            logger.debug(f"Checkpoint state: {ctx.state.value}")

            ctx_dict = ctx.to_dict()
            events = ctx_dict.get("events") or []
            if ctx.is_transient:
                # Only the outer lifecycle persists: workflow-level events stay
                # (they carry the terminal state and output); task-level events
                # exist in memory only.
                events = [e for e in events if not str(e.get("type", "")).startswith("TASK_")]
                ctx_dict["events"] = events
            if exclude_event_ids:
                events = [e for e in events if e.get("id") not in exclude_event_ids]
                ctx_dict["events"] = events
            sent_ids = [e.get("id") for e in events if e.get("id")]
            logger.debug(
                f"Sending checkpoint delta: {len(events)} event(s), {len(str(ctx_dict))} bytes",
            )

            headers = {}
            generation = self._claim_generations.get(ctx.execution_id)
            if generation is not None:
                headers["X-Flux-Claim-Generation"] = generation

            checkpoint_start = time.monotonic()
            response = await self._authorized_post(
                f"{base_url}/checkpoint/{ctx.execution_id}",
                json=ctx_dict,
                headers=headers,
            )
            if response.status_code == 409 and "stale-claim" in response.text:
                raise StaleClaimError(ctx.execution_id)
            response.raise_for_status()
            checkpoint_duration = time.monotonic() - checkpoint_start
            response_data = response.json()

            logger.debug(f"Checkpoint response: {response.status_code}")
            logger.debug(f"Response data: {response_data}")
            logger.info(
                f"Checkpoint for execution '{ctx.workflow_name}' ({ctx.execution_id}) completed successfully",
            )

            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_checkpoint(ctx.workflow_namespace, ctx.workflow_name, checkpoint_duration)
            return sent_ids
        except Exception as e:
            logger.error(f"Error during checkpoint: {str(e)}")
            logger.debug(f"Checkpoint error details: {type(e).__name__}: {str(e)}")
            raise

    async def _flush_progress(self, queue: asyncio.Queue, execution_id: str):
        base_url = f"{self.base_url}/{self.name}"
        try:
            while True:
                batch = []
                try:
                    item = await queue.get()
                    batch.append(item)
                    while not queue.empty() and len(batch) < 50:
                        batch.append(queue.get_nowait())
                except asyncio.CancelledError:
                    while not queue.empty():
                        batch.append(queue.get_nowait())
                    if batch:
                        try:
                            await self._authorized_post(
                                f"{base_url}/progress/{execution_id}",
                                json=batch,
                            )
                        except Exception:
                            logger.warning(
                                "Dropped %d progress event(s) for %s on cancel-flush",
                                len(batch),
                                execution_id,
                                exc_info=True,
                            )
                    return

                try:
                    await self._authorized_post(
                        f"{base_url}/progress/{execution_id}",
                        json=batch,
                    )
                except Exception:
                    logger.warning(
                        "Dropped %d progress event(s) for %s",
                        len(batch),
                        execution_id,
                        exc_info=True,
                    )
        except Exception:
            logger.error(
                "Progress flusher for %s exited unexpectedly",
                execution_id,
                exc_info=True,
            )

    def _setup_progress(self, ctx):
        queue = asyncio.Queue(maxsize=1000)
        self._progress_queues[ctx.execution_id] = queue

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            self._progress_flushers[ctx.execution_id] = asyncio.create_task(
                self._flush_progress(queue, ctx.execution_id),
            )
        else:
            self._progress_flushers[ctx.execution_id] = None

        ctx.set_progress_callback(self._record_progress)

    def _record_progress(self, execution_id, task_id, task_name, value):
        """Enqueue one progress update for batched delivery to the server.

        Reached two ways: as the context progress callback for in-process
        executions, and as the RunnerHooks.progress relay for progress frames
        arriving from runner child processes.
        """
        q = self._progress_queues.get(execution_id)
        if q:
            try:
                q.put_nowait(
                    {
                        "task_id": task_id,
                        "task_name": task_name,
                        "value": value,
                    },
                )
            except asyncio.QueueFull:
                pass

    async def _teardown_progress(self, execution_id: str):
        flusher = self._progress_flushers.pop(execution_id, None)
        if flusher and not flusher.done():
            flusher.cancel()
            try:
                await flusher
            except asyncio.CancelledError:
                pass
        self._progress_queues.pop(execution_id, None)

    async def _get_runtime_info(self):
        logger.debug("Gathering runtime information")
        runtime_info = {
            "os_name": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
        }
        logger.debug(f"Runtime info: {runtime_info}")
        return runtime_info

    async def _get_resources_info(self):
        logger.debug("Gathering system resource information")

        logger.debug("Getting CPU information")
        cpu_total = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_available = cpu_total * (100 - cpu_percent) / 100
        logger.debug(f"CPU: total={cpu_total}, usage={cpu_percent}%, available={cpu_available:.2f}")

        logger.debug("Getting memory information")
        memory = psutil.virtual_memory()
        memory_total = memory.total
        memory_available = memory.available
        logger.debug(
            f"Memory: total={memory_total}, available={memory_available}, percent={memory.percent}%",
        )

        logger.debug("Getting disk information")
        disk = psutil.disk_usage("/")
        disk_total = disk.total
        disk_free = disk.free
        logger.debug(f"Disk: total={disk_total}, free={disk_free}, percent={disk.percent}%")

        logger.debug("Getting GPU information")
        gpus = await self._get_gpu_info()

        resources = {
            "cpu_total": cpu_total,
            "cpu_available": cpu_available,
            "memory_total": memory_total,
            "memory_available": memory_available,
            "disk_total": disk_total,
            "disk_free": disk_free,
            "gpus": gpus,
        }

        logger.debug(f"Collected resource information: {len(gpus)} GPUs found")
        return resources

    async def _get_gpu_info(self):
        logger.debug("Collecting GPU information")
        try:
            import GPUtil
        except (ImportError, ModuleNotFoundError):
            logger.debug("GPUtil not available, skipping GPU info")
            return []

        gpus = []
        gpu_devices = GPUtil.getGPUs()
        logger.debug(f"Found {len(gpu_devices)} GPU devices")

        for i, gpu in enumerate(gpu_devices):
            logger.debug(
                f"GPU {i + 1}: {gpu.name}, Memory: {gpu.memoryTotal}MB, Free: {gpu.memoryFree}MB",
            )
            gpus.append(
                {
                    "name": gpu.name,
                    "memory_total": gpu.memoryTotal,
                    "memory_available": gpu.memoryFree,
                },
            )
        return gpus

    async def _get_installed_packages(self):
        logger.debug("Collecting installed packages information")
        import importlib.metadata

        # TODO: use poetry package groups to load a specific set of packages that are available in the worker environment for execution
        packages = []
        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name")
            if name:  # Only include packages with a valid name
                packages.append({"name": name, "version": dist.version})

        logger.debug(f"Collected information for {len(packages)} installed packages")
        return packages


if __name__ == "__main__":  # pragma: no cover
    from uuid import uuid4
    from flux.utils import configure_logging

    configure_logging()
    settings = Configuration.get().settings
    worker_name = f"worker-{uuid4().hex[-6:]}"
    server_url = settings.workers.server_url

    logger.debug(f"Starting worker with name: {worker_name}")
    logger.debug(f"Server URL: {server_url}")
    logger.debug(
        f"Bootstrap token configured: {'Yes' if settings.workers.bootstrap_token else 'No'}",
    )

    Worker(name=worker_name, server_url=server_url).start()
