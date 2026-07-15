"""Agent-facing dynamic workflow tasks: ``create_workflow`` / ``run_workflow``.

Both run inside a workflow (typically an agent loop) and authenticate to the
server with the calling execution's own token — the credential the child
already holds — so authorization lands on the agent's grants and the server
derives the ``dyn-*`` namespace from that identity. Registration is
idempotent by source hash, which also makes ``create_workflow`` replay-safe:
a resumed workflow re-registering identical source gets the same entry back.

See docs/specs/2026-07-15-dynamic-workflows-spec.md.
"""

from __future__ import annotations

from typing import Any, Literal

from flux.config import Configuration
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.domain.execution_context import ExecutionContext
from flux.errors import ExecutionError
from flux.task import task
from flux.utils import get_logger

logger = get_logger(__name__)


async def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    try:
        current = await ExecutionContext.get()
    except Exception:
        return headers
    if current is not None:
        if current.exec_token:
            headers["Authorization"] = f"Bearer {current.exec_token}"
        # Sticky-routing hint, same as call(): prefer this worker while
        # eligible (warm module cache for repeated dynamic runs).
        if current.current_worker:
            headers["X-Flux-Preferred-Worker"] = current.current_worker
    return headers


@task
async def create_workflow(source: str) -> dict[str, Any]:
    """Register agent-authored workflow source; returns
    ``{namespace, name, version, existing}``.

    Rejections (policy violations, quota, size) raise ExecutionError with
    the server's actionable message.
    """
    import httpx

    settings = Configuration.get().settings
    headers = await _auth_headers()
    url = f"{settings.workers.server_url}/workflows/dynamic"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json={"source": source}, headers=headers)
    except httpx.ConnectError as ex:
        raise ExecutionError(
            message=f"Could not connect to the Flux server at {settings.workers.server_url}.",
        ) from ex
    if response.status_code == 422:
        detail = response.json().get("detail") or {}
        raise ExecutionError(
            message=f"Dynamic workflow rejected: {detail.get('message', response.text)}",
        )
    if response.status_code in (403, 404):
        raise ExecutionError(
            message=(
                "Dynamic workflows are not enabled for this deployment or "
                f"this identity (HTTP {response.status_code})"
            ),
        )
    response.raise_for_status()
    return response.json()


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
    import httpx

    if (source is None) == (ref is None):
        raise ValueError("provide exactly one of 'source' or 'ref'")
    if mode not in ("sync", "async"):
        raise ValueError(f"mode must be 'sync' or 'async', got: '{mode}'")

    if source is not None:
        registered = await create_workflow(source)
        namespace, name = registered["namespace"], registered["name"]
    else:
        assert ref is not None
        namespace, _, name = ref.partition("/")
        if not namespace or not name:
            raise ValueError(f"ref must be 'namespace/name', got: '{ref}'")

    settings = Configuration.get().settings
    headers = await _auth_headers()
    url = f"{settings.workers.server_url}/workflows/{namespace}/{name}/run/{mode}"

    # Mirrors flux.tasks.call's response handling, plus the Authorization
    # header a dyn-* run needs when auth is enabled.
    try:
        async with httpx.AsyncClient(timeout=settings.workers.default_timeout) as client:
            if mode == "async":
                response = await client.post(url, json=input, headers=headers)
                response.raise_for_status()
                return response.json()["execution_id"]

            response = await client.post(f"{url}?detailed=true", json=input, headers=headers)
            response.raise_for_status()
            data = response.json()
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
                    f"Dynamic workflow {namespace}/{name} finished in state "
                    f"{ctx.state.value}; pause/approval flows are not supported "
                    "through run_workflow"
                ),
            )
    except httpx.ConnectError as ex:
        raise ExecutionError(
            message=f"Could not connect to the Flux server at {settings.workers.server_url}.",
        ) from ex
    except httpx.HTTPStatusError as ex:
        raise ExecutionError(
            message=(
                f"Running dynamic workflow {namespace}/{name} failed: "
                f"HTTP {ex.response.status_code}: {ex.response.text[:500]}"
            ),
        ) from ex
