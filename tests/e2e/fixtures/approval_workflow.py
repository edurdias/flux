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
