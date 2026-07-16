"""Fixture workflows for the service-socket e2e tests.

``service_roundtrip`` is affinity-pinned to workers advertising the plain
``svc=echo`` label (the e2e harness injects ``FLUX_SERVICE_SOCKETS`` into
that worker's environment; the subprocess child inherits it through the
sanitized-env passthrough, exactly as an airgapped child receives it from
the runner's ``--env``).
"""

from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.tasks import service_client, service_socket
from flux.workflow import workflow


@task
async def call_echo_service() -> dict:
    async with service_client("echo") as client:
        response = await client.get("/classify")
        response.raise_for_status()
        return response.json()


@workflow.with_options(affinity={"svc": "echo"})
async def service_roundtrip(ctx: ExecutionContext):
    return await call_echo_service()


@task
async def resolve_missing_service() -> str:
    return service_socket("not-granted-anywhere")


@workflow
async def service_missing(ctx: ExecutionContext):
    return await resolve_missing_service()
