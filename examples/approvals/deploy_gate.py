"""Deploy gate — illustrates ``requires_approval`` as a workflow primitive.

A production deploy task that requires human approval before running. The
workflow does prep work first (build + smoke-tests), then pauses on the
gated step. Approval is conditional: only ``environment="prod"`` triggers
the gate; staging deploys go through unattended.

Usage:
    poetry run python examples/approvals/deploy_gate.py
    # When the workflow pauses, in another terminal:
    flux execution approve <execution_id> <task_call_id> --reason "lgtm"

Or inline:
    from examples.approvals.deploy_gate import deploy_workflow
    ctx = deploy_workflow.run({"environment": "prod"})
"""

from __future__ import annotations

from flux import ExecutionContext, task, workflow


@task
async def build_artifact(environment: str) -> str:
    return f"artifact-{environment}-1.0.0"


@task
async def run_smoke_tests(artifact: str) -> bool:
    return True


@task.with_options(
    requires_approval=lambda environment, **_: environment == "prod",
)
async def deploy_to_environment(*, environment: str, artifact: str) -> str:
    return f"deployed {artifact} to {environment}"


@task
async def announce(deployment: str) -> None:
    return None


@workflow
async def deploy_workflow(ctx: ExecutionContext[dict]):
    raw = ctx.input or {}
    environment = raw.get("environment", "staging")

    artifact = await build_artifact(environment)
    if not await run_smoke_tests(artifact):
        return {"status": "failed", "stage": "smoke_tests"}

    deployment = await deploy_to_environment(environment=environment, artifact=artifact)
    await announce(deployment)
    return {"status": "ok", "deployment": deployment}


if __name__ == "__main__":  # pragma: no cover
    ctx = deploy_workflow.run({"environment": "prod"})
    if ctx.is_paused:
        print(f"Paused for approval. execution_id={ctx.execution_id}")
        print("Approve via: flux execution approvals --execution <id> --json")
    elif ctx.has_succeeded:
        print(f"Deployed: {ctx.output}")
    else:
        print(f"Failed: {ctx.output}")
