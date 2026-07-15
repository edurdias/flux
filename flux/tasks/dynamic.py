"""Agent-facing dynamic workflow tasks: ``create_workflow`` / ``run_workflow``.

Both run inside a workflow (typically an agent loop) and talk to the server
through :class:`flux.client.FluxClient`, carrying the calling execution's
own token — the credential the child already holds — so authorization lands
on the agent's grants and the server derives the ``dyn-*`` namespace from
that identity. Registration is idempotent by source hash, which also makes
``create_workflow`` replay-safe: a resumed workflow re-registering identical
source gets the same entry back.

See docs/specs/2026-07-15-dynamic-workflows-spec.md.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from flux.client import FluxClient
from flux.config import Configuration
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.domain.execution_context import ExecutionContext
from flux.errors import ExecutionError
from flux.task import task
from flux.utils import get_logger

logger = get_logger(__name__)


async def _client() -> FluxClient:
    """A FluxClient carrying the calling execution's credentials/hints."""
    settings = Configuration.get().settings
    headers: dict[str, str] = {}
    try:
        current = await ExecutionContext.get()
    except Exception:
        current = None
    if current is not None:
        if current.exec_token:
            headers["Authorization"] = f"Bearer {current.exec_token}"
        # Sticky-routing hint, same as call(): prefer this worker while
        # eligible (warm module cache for repeated dynamic runs).
        if current.current_worker:
            headers["X-Flux-Preferred-Worker"] = current.current_worker
    return FluxClient(
        settings.workers.server_url,
        timeout=settings.workers.default_timeout or None,
        headers=headers or None,
    )


def _registration_error(ex: httpx.HTTPStatusError) -> ExecutionError:
    if ex.response.status_code == 422:
        detail: dict[str, Any] = {}
        try:
            detail = ex.response.json().get("detail") or {}
        except ValueError:
            pass
        return ExecutionError(
            message=f"Dynamic workflow rejected: {detail.get('message', ex.response.text[:500])}",
        )
    if ex.response.status_code in (403, 404):
        return ExecutionError(
            message=(
                "Dynamic workflows are not enabled for this deployment or "
                f"this identity (HTTP {ex.response.status_code})"
            ),
        )
    return ExecutionError(
        message=(
            f"Dynamic workflow registration failed: HTTP "
            f"{ex.response.status_code}: {ex.response.text[:500]}"
        ),
    )


@task
async def create_workflow(source: str) -> dict[str, Any]:
    """Register agent-authored workflow source; returns
    ``{namespace, name, version, existing}``.

    Rejections (policy violations, quota, size) raise ExecutionError with
    the server's actionable message.
    """
    async with await _client() as client:
        try:
            return await client.register_dynamic_workflow(source)
        except httpx.HTTPStatusError as ex:
            raise _registration_error(ex) from ex
        except httpx.ConnectError as ex:
            raise ExecutionError(
                message=f"Could not connect to the Flux server at {client.server_url}.",
            ) from ex


@task
async def run_workflow(
    source: str | None = None,
    ref: str | None = None,
    input: Any = None,
    mode: Literal["sync", "async"] = "sync",
) -> Any:
    """Run a dynamic workflow: by ``source`` (register-then-run, idempotent)
    or by ``ref`` (``namespace/name`` from an earlier ``create_workflow``).

    ``sync`` waits and returns the workflow's output (raising ExecutionError
    on failure); ``async`` returns the execution id.
    """
    if (source is None) == (ref is None):
        raise ValueError("provide exactly one of 'source' or 'ref'")
    if mode not in ("sync", "async"):
        raise ValueError(f"mode must be 'sync' or 'async', got: '{mode}'")

    if source is not None:
        registered = await create_workflow(source)
        workflow_ref = f"{registered['namespace']}/{registered['name']}"
    else:
        assert ref is not None
        namespace, _, name = ref.partition("/")
        if not namespace or not name:
            raise ValueError(f"ref must be 'namespace/name', got: '{ref}'")
        workflow_ref = ref

    async with await _client() as client:
        try:
            if mode == "async":
                data = await client.run_workflow(workflow_ref, input)
                return data["execution_id"]

            data = await client.run_workflow_sync(workflow_ref, input, detailed=True)
        except httpx.ConnectError as ex:
            raise ExecutionError(
                message=f"Could not connect to the Flux server at {client.server_url}.",
            ) from ex
        except httpx.HTTPStatusError as ex:
            raise ExecutionError(
                message=(
                    f"Running dynamic workflow {workflow_ref} failed: "
                    f"HTTP {ex.response.status_code}: {ex.response.text[:500]}"
                ),
            ) from ex

    ctx: ExecutionContext = ExecutionContext(
        workflow_id=data["workflow_id"],
        workflow_namespace=data.get("workflow_namespace", "default"),
        workflow_name=data["workflow_name"],
        input=data["input"],
        execution_id=data["execution_id"],
        state=data["state"],
        events=[
            ExecutionEvent(
                type=ExecutionEventType(event["type"]),
                source_id=event["source_id"],
                name=event["name"],
                value=event.get("value"),
            )
            for event in data["events"]
        ],
        requests=data.get("requests", []),
    )
    if ctx.has_succeeded:
        return ctx.output
    if ctx.has_failed:
        raise ExecutionError(ctx.output)
    raise ExecutionError(
        message=(
            f"Dynamic workflow {workflow_ref} finished in state "
            f"{ctx.state.value}; pause/approval flows are not supported "
            "through run_workflow"
        ),
    )
