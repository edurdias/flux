"""Standing approvals — approve a gated task once for the whole execution.

A multi-region rollout where every region's deploy is approval-gated. By
default each gated call pauses the workflow and waits for its own
approval. Approving the first one with ``--always`` records a standing
grant: the remaining regions auto-approve without pausing, and each still
leaves its own approved audit row (reason "standing grant").

Usage:
    poetry run python examples/approvals/standing_grant.py
    # When the workflow pauses, in another terminal:
    flux execution approve <execution_id> <task_call_id> --always
    # ...then run this script's resume step, or:
    flux workflow status rollout_workflow <execution_id>

Or inline:
    from examples.approvals.standing_grant import rollout_workflow
    ctx = rollout_workflow.run(["us-east", "eu-west", "ap-south"])
"""

from __future__ import annotations

from flux import ExecutionContext, task, workflow


@task.with_options(requires_approval=True)
async def deploy_region(region: str) -> str:
    return f"deployed to {region}"


@workflow
async def rollout_workflow(ctx: ExecutionContext[list[str]]):
    regions = ctx.input or ["us-east", "eu-west", "ap-south"]
    results = []
    for region in regions:
        results.append(await deploy_region(region))
    return results


if __name__ == "__main__":  # pragma: no cover
    ctx = rollout_workflow.run()
    if ctx.is_paused:
        print(f"Paused for approval. execution_id={ctx.execution_id}")
        print("List the pending gate:  flux execution approvals --execution <id> --json")
        print("Grant it for the whole execution:")
        print("  flux execution approve <execution_id> <task_call_id> --always")
        print("Then resume: the remaining regions deploy without pausing again.")
    elif ctx.has_succeeded:
        print(f"Rollout complete: {ctx.output}")
    else:
        print(f"Failed: {ctx.output}")
