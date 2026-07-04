"""Subprocess runner child entrypoint (``python -m flux.runners.child``).

Reads one JSON request line from stdin, executes the workflow, and streams
JSON-line frames back over stdout:

- ``{"type": "checkpoint", "transient": bool, "context": {...}}``
- ``{"type": "progress", "execution_id": ..., "task_id": ..., "task_name": ..., "value": ...}``
- ``{"type": "secrets_request" | "configs_request", "id": N, "names": [...]}``
- ``{"type": "result", "transient": bool, "context": {...}}``
- ``{"type": "fatal", "error": "..."}`` (child-infrastructure failure)

The parent answers RPC frames on the child's stdin:
``{"type": "rpc_response", "id": N, "values": {...}}`` or ``{..., "error": "..."}``.

The child never talks to the server and holds no credentials: checkpoints,
progress, secrets, and configs all flow through the parent. Stdout is
reserved for frames — logging is forced to stderr.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sys
from typing import Any

from flux.config_manager import ConfigManager
from flux.secret_managers import SecretManager
from flux.utils import get_logger

logger = get_logger(__name__)


class _FrameIO:
    """JSON-line framing over the child's stdio.

    Writes are serialized under a lock and run in a thread so a slow pipe
    never blocks the event loop; reads dispatch RPC responses to their
    waiting futures. EOF on stdin means the parent died — the workflow task
    is cancelled so the orphan exits instead of running detached.
    """

    def __init__(self) -> None:
        self._out_lock = asyncio.Lock()
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def emit(self, frame: dict) -> None:
        data = json.dumps(frame, default=str) + "\n"
        async with self._out_lock:
            await asyncio.to_thread(self._write, data)

    @staticmethod
    def _write(data: str) -> None:
        sys.stdout.write(data)
        sys.stdout.flush()

    async def call(self, frame_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        rpc_id = self._seq
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = future
        try:
            await self.emit({"type": frame_type, "id": rpc_id, **payload})
            return await future
        finally:
            self._pending.pop(rpc_id, None)

    async def read_responses(self, on_eof) -> None:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                on_eof()
                return
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Dropping malformed frame from parent")
                continue
            future = self._pending.get(frame.get("id"))
            if future is None or future.done():
                continue
            if frame.get("error") is not None:
                future.set_exception(ValueError(frame["error"]))
            else:
                future.set_result(frame.get("values") or {})


class _PipeConfigManager(ConfigManager):
    """Resolves config_requests through the parent worker."""

    def __init__(self, io: _FrameIO):
        self._io = io

    async def get(self, config_requests: list[str]) -> dict[str, Any]:
        return await self._io.call("configs_request", {"names": config_requests})

    async def aclose(self) -> None:
        return None

    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError("Config writes are not available inside a runner child")

    def remove(self, name: str) -> None:
        raise NotImplementedError("Config writes are not available inside a runner child")

    def all(self) -> list[str]:
        raise NotImplementedError("Config listing is not available inside a runner child")


class _PipeSecretManager(SecretManager):
    """Resolves secret_requests through the parent worker."""

    def __init__(self, io: _FrameIO):
        self._io = io

    async def get(self, secret_requests: list[str]) -> dict[str, Any]:
        return await self._io.call("secrets_request", {"names": secret_requests})

    async def aclose(self) -> None:
        return None

    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError("Secret writes are not available inside a runner child")

    def remove(self, name: str) -> None:
        raise NotImplementedError("Secret writes are not available inside a runner child")

    def all(self) -> list[str]:
        raise NotImplementedError("Secret listing is not available inside a runner child")


class _PipeApprovalStore:
    """Approval gate operations relayed through the parent worker."""

    def __init__(self, io: _FrameIO):
        self._io = io

    async def get_by_call(self, execution_id: str, task_call_id: str):
        from flux.approvals import ApprovalSnapshot

        values = await self._io.call(
            "approval_get_request",
            {"execution_id": execution_id, "task_call_id": task_call_id},
        )
        data = values.get("approval")
        return ApprovalSnapshot.from_dict(data) if data else None

    async def register(self, ctx, task_call_id: str, task_name: str, awaiting_event) -> str:
        values = await self._io.call(
            "approval_register_request",
            {
                "execution_id": ctx.execution_id,
                "task_call_id": task_call_id,
                "task_name": task_name,
            },
        )
        status = values.get("status", "cancelled")
        if status in ("created", "exists"):
            # Reaches the server through the normal checkpoint path.
            ctx.events.append(awaiting_event)
        return status


async def _run(request: dict) -> int:
    from flux.domain.execution_context import ExecutionContext
    from flux.remote_managers import set_remote_managers
    from flux.runners.loader import WorkflowModuleLoader, find_workflow

    io = _FrameIO()

    async def checkpoint(ctx: ExecutionContext) -> None:
        if ctx.is_transient and ctx.is_paused and not ctx.has_finished:
            # Same conversion the worker applies: pause needs task-level
            # history a transient run does not persist. Convert on the real
            # context (it lives here, not in the parent) so exactly one
            # terminal FAILED reaches the server.
            from flux.errors import TransientDurabilityError

            error = TransientDurabilityError(ctx.execution_id, "pause")
            logger.warning(str(error))
            ctx.fail(
                ctx.execution_id,
                {"type": "TransientDurabilityError", "message": str(error)},
            )
        await io.emit(
            {
                "type": "checkpoint",
                "transient": ctx.is_transient,
                "context": ctx.to_dict(),
            },
        )

    ctx = ExecutionContext.from_json(request["context"], checkpoint)
    if request.get("transient"):
        ctx.mark_transient()
    if request.get("exec_token"):
        ctx.set_exec_token(request["exec_token"])

    def on_progress(execution_id, task_id, task_name, value):
        frame = {
            "type": "progress",
            "execution_id": execution_id,
            "task_id": task_id,
            "task_name": task_name,
            "value": value,
        }
        task = asyncio.get_running_loop().create_task(io.emit(frame))

        def _consume(t: asyncio.Task) -> None:
            # Retrieve the exception so it never warns as unretrieved, but a
            # cancelled task has none to retrieve (exception() would raise).
            if not t.cancelled():
                t.exception()

        task.add_done_callback(_consume)

    ctx.set_progress_callback(on_progress)
    set_remote_managers(
        config=_PipeConfigManager(io),
        secret=_PipeSecretManager(io),
        approvals=_PipeApprovalStore(io),
    )

    definition = request["workflow"]
    # No cache: this process executes exactly one workflow and exits.
    loader = WorkflowModuleLoader(ttl=0)
    module = loader.load(
        definition.get("namespace", "default"),
        definition["name"],
        definition["version"],
        definition["source"],
    )
    wfunc = find_workflow(module, definition.get("namespace", "default"), definition["name"])
    if wfunc is None:
        await io.emit(
            {
                "type": "fatal",
                "error": f"Workflow {definition['name']} not found in module",
            },
        )
        return 3

    wf_task = asyncio.create_task(wfunc(ctx))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Parent-initiated cancellation: cancel the workflow so its own
        # CancelledError handling (terminal CANCELLED checkpoint) runs.
        loop.add_signal_handler(sig, wf_task.cancel)

    reader = asyncio.create_task(io.read_responses(on_eof=wf_task.cancel))
    try:
        result = await wf_task
    except asyncio.CancelledError:
        result = ctx
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader

    await io.emit(
        {
            "type": "result",
            "transient": result.is_transient,
            "context": result.to_dict(),
        },
    )
    return 0


def main() -> int:
    # Stdout carries frames; force every log record to stderr.
    logging.basicConfig(stream=sys.stderr, force=True)

    line = sys.stdin.readline()
    if not line:
        return 2
    try:
        request = json.loads(line)
    except json.JSONDecodeError as e:
        print(json.dumps({"type": "fatal", "error": f"Malformed request: {e}"}), flush=True)
        return 2
    try:
        return asyncio.run(_run(request))
    except Exception as e:  # pragma: no cover - crash reporting of last resort
        logger.exception("Runner child failed")
        with contextlib.suppress(Exception):
            print(json.dumps({"type": "fatal", "error": f"{type(e).__name__}: {e}"}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
