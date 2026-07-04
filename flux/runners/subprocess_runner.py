"""Runner that executes each workflow in its own child process.

The default runner: a crash, OOM, or event-loop-blocking call in one
workflow cannot take down the worker or its co-resident executions, and
cancellation is enforceable with signals even against code stuck in sync C
calls. See ``flux/runners/child.py`` for the wire protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from typing import TYPE_CHECKING, Any

from flux.errors import WorkerProcessCrashed
from flux.runners.base import Runner, RunnerHooks
from flux.utils import get_logger

if TYPE_CHECKING:
    from flux.domain.execution_context import ExecutionContext
    from flux.worker import WorkflowExecutionRequest

logger = get_logger(__name__)

# Checkpoint frames carry full context snapshots; the default asyncio stream
# limit (64 KiB) would reject them as over-long lines.
_STREAM_LIMIT = 64 * 1024 * 1024

# Worker-process credentials the execution child must never see. Everything
# the child needs (secrets, configs, approvals, checkpoints) flows over the
# pipe through the parent; the only credential it holds is the short-lived,
# single-execution token delivered in the request frame.
_SENSITIVE_ENV_VARS = frozenset(
    {
        "FLUX_WORKERS__BOOTSTRAP_TOKEN",  # fleet-wide registration secret
        "FLUX_DATABASE_URL",  # may embed database credentials
    },
)
_SENSITIVE_ENV_PREFIXES = ("FLUX_SECURITY__",)  # encryption key, token secrets, auth config


def child_environment() -> dict[str, str]:
    """The worker's environment minus credentials workflow code must not hold."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _SENSITIVE_ENV_VARS and not key.startswith(_SENSITIVE_ENV_PREFIXES)
    }


class SubprocessRunner(Runner):
    """Also the base for other pipe-speaking runners (e.g. Docker): they run
    the same child protocol and override only ``_spawn`` (how the child
    process is launched) and ``_force_kill``/``_reap`` (how it is destroyed).
    """

    name = "subprocess"

    def __init__(self, term_grace: float = 10.0, memory_limit: int = 0):
        self._term_grace = term_grace
        self._memory_limit = memory_limit

    async def _spawn(self, request: WorkflowExecutionRequest):
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "flux.runners.child",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
            env=child_environment(),
            preexec_fn=self._build_preexec(),
        )

    async def _force_kill(self, proc):
        with contextlib.suppress(ProcessLookupError):
            # Best-effort: the child may have exited between the grace
            # timeout and the kill.
            proc.kill()

    def _reap(self, proc):
        """Called once the child is gone; subclasses release launch state."""

    async def execute(
        self,
        request: WorkflowExecutionRequest,
        hooks: RunnerHooks,
    ) -> ExecutionContext:
        execution_id = request.context.execution_id
        proc = await self._spawn(request)
        assert proc.stdin and proc.stdout and proc.stderr

        stderr_pump = asyncio.create_task(self._pump_stderr(proc.stderr, execution_id))
        stdin_lock = asyncio.Lock()
        rpc_tasks: set[asyncio.Task] = set()
        last_ctx: ExecutionContext | None = None
        result_ctx: ExecutionContext | None = None

        request_frame = {
            "workflow": request.workflow.model_dump(),
            "context": request.context.to_dict(),
            "exec_token": request.exec_token,
            "transient": request.context.is_transient,
        }
        try:
            async with stdin_lock:
                proc.stdin.write(json.dumps(request_frame, default=str).encode() + b"\n")
                await proc.stdin.drain()

            async for frame in self._frames(proc):
                kind = frame.get("type")
                if kind == "checkpoint":
                    last_ctx = self._rebuild(frame, hooks)
                    await hooks.checkpoint(last_ctx)
                elif kind == "progress":
                    if hooks.progress:
                        hooks.progress(
                            frame["execution_id"],
                            frame["task_id"],
                            frame["task_name"],
                            frame["value"],
                        )
                elif kind in (
                    "secrets_request",
                    "configs_request",
                    "approval_get_request",
                    "approval_register_request",
                ):
                    task = asyncio.create_task(
                        self._serve_rpc(proc, frame, hooks, stdin_lock),
                    )
                    rpc_tasks.add(task)
                    task.add_done_callback(rpc_tasks.discard)
                elif kind == "result":
                    result_ctx = self._rebuild(frame, hooks)
                    break
                elif kind == "fatal":
                    logger.error(
                        f"Runner child for {execution_id} reported: {frame.get('error')}",
                    )
                    break
            self._close_stdin(proc)
            await proc.wait()
        except asyncio.CancelledError:
            # Cancellation or drain deadline: signal the child so the
            # workflow's own cancellation handling (terminal CANCELLED
            # checkpoint, forwarded here) still runs, then enforce.
            await self._shutdown(proc, hooks)
            raise
        finally:
            self._close_stdin(proc)
            stderr_pump.cancel()
            for task in rpc_tasks:
                task.cancel()
            self._reap(proc)

        if result_ctx is None:
            raise WorkerProcessCrashed(
                execution_id,
                exit_code=proc.returncode,
                last_context=last_ctx or request.context,
            )
        return result_ctx

    async def _frames(self, proc):
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Dropping malformed frame from runner child")

    def _rebuild(self, frame: dict, hooks: RunnerHooks) -> ExecutionContext:
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext.from_json(frame["context"], hooks.checkpoint)
        if frame.get("transient"):
            ctx.mark_transient()
        return ctx

    async def _serve_rpc(self, proc, frame: dict, hooks: RunnerHooks, stdin_lock: asyncio.Lock):
        response: dict[str, Any] = {"type": "rpc_response", "id": frame["id"]}
        try:
            response["values"] = await self._resolve_rpc(frame, hooks)
        except Exception as e:
            response["error"] = str(e)
        try:
            async with stdin_lock:
                proc.stdin.write(json.dumps(response, default=str).encode() + b"\n")
                await proc.stdin.drain()
        except (ConnectionError, RuntimeError):
            logger.debug("Runner child went away before its RPC response was written")

    async def _resolve_rpc(self, frame: dict, hooks: RunnerHooks) -> dict[str, Any]:
        kind = frame["type"]
        if kind == "secrets_request":
            return await hooks.get_secrets(frame.get("names") or [])
        if kind == "configs_request":
            return await hooks.get_configs(frame.get("names") or [])
        if kind == "approval_get_request":
            if hooks.get_approval is None:
                raise RuntimeError("Approval lookups are not available for this execution")
            approval = await hooks.get_approval(
                frame["execution_id"],
                frame["task_call_id"],
            )
            return {"approval": approval}
        if kind == "approval_register_request":
            if hooks.register_approval is None:
                raise RuntimeError("Approval registration is not available for this execution")
            return await hooks.register_approval(
                frame["execution_id"],
                {
                    "task_call_id": frame["task_call_id"],
                    "task_name": frame["task_name"],
                },
            )
        raise RuntimeError(f"Unknown RPC frame type: {kind}")

    @staticmethod
    def _close_stdin(proc):
        # The child reads stdin in a worker thread; without EOF that thread
        # never returns and blocks the child's event-loop shutdown.
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()

    async def _shutdown(self, proc, hooks: RunnerHooks):
        if proc.returncode is not None:
            return
        self._close_stdin(proc)
        proc.terminate()
        try:
            # Keep forwarding frames during the grace window so the child's
            # terminal CANCELLED checkpoint reaches the server.
            await asyncio.wait_for(self._drain_frames(proc, hooks), timeout=self._term_grace)
        except TimeoutError:
            # Either the child ignored SIGTERM, or it exited but its terminal
            # checkpoint is still awaiting server acknowledgement (the outbox
            # keeps retrying in the background either way).
            logger.warning(
                "Runner child did not finish within the termination grace "
                "period; force-killing (a no-op if it already exited)",
            )
            await self._force_kill(proc)
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()

    async def _drain_frames(self, proc, hooks: RunnerHooks):
        async for frame in self._frames(proc):
            if frame.get("type") == "checkpoint" or frame.get("type") == "result":
                with contextlib.suppress(Exception):
                    await hooks.checkpoint(self._rebuild(frame, hooks))
        await proc.wait()

    async def _pump_stderr(self, stream, execution_id: str):
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                line = await stream.readline()
                if not line:
                    return
                logger.info(f"[child {execution_id}] {line.decode(errors='replace').rstrip()}")

    def _build_preexec(self):
        if not self._memory_limit or sys.platform not in ("linux", "linux2"):
            return None
        limit = self._memory_limit

        def _apply_limits():  # pragma: no cover - runs in the forked child
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

        return _apply_limits
