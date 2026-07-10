"""Fixture workflow for the approval-primitive E2E.

Two-task workflow: a setup step writes a value, then a gated `deploy` step
needs human approval before running. On approval the workflow completes
with the gated task's output. On rejection the workflow fails.
"""

from __future__ import annotations

from flux import task, workflow


@task
async def prepare(value: str) -> str:
    return f"prepared:{value}"


@task.with_options(requires_approval=True)
async def deploy(payload: str) -> str:
    return f"deployed:{payload}"


@workflow
async def approval_e2e(ctx):
    prepared = await prepare(ctx.input or "default")
    return await deploy(prepared)


@task.with_options(requires_approval=True, retry_max_attempts=1, retry_delay=1)
async def flaky_deploy(path: str) -> str:
    """Fails on the first attempt; the attempt count lives in a marker file
    so the e2e can observe exactly how many times the body ran across the
    worker's execution processes."""
    from pathlib import Path

    marker = Path(path)
    attempts = int(marker.read_text()) + 1 if marker.exists() else 1
    marker.write_text(str(attempts))
    if attempts == 1:
        raise RuntimeError("first attempt fails")
    return f"deployed-after-{attempts}-attempts"


@workflow
async def approval_retry_e2e(ctx):
    return await flaky_deploy(ctx.input)
