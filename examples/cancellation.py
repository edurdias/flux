"""Example demonstrating workflow cancellation.

Runs a cancellable workflow inline and cancels it mid-flight. Cancellation
in Flux is cooperative asyncio cancellation: tasks receive
``asyncio.CancelledError`` at their next await point, may clean up, and
re-raise; the workflow records the terminal ``CANCELLED`` state.

In a deployed system cancellation is server-mediated — request it with
``flux workflow cancel <workflow> <execution_id>``,
``FluxClient.cancel_execution(...)``, or
``GET /workflows/{ns}/{name}/cancel/{execution_id}`` — and the worker
running the execution delivers exactly the cancellation shown here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def long_running_task(iterations: int = 10):
    """A long running task that can be cancelled."""
    results = []
    try:
        for i in range(iterations):
            # Simulate work
            await asyncio.sleep(1)
            print(f"[TASK] Completed iteration {i + 1}/{iterations}")
            results.append(i)
        print("[TASK] Task completed all iterations")
        return results
    except asyncio.CancelledError:
        print("[TASK] Task was cancelled")
        raise


@workflow
async def cancellable_workflow(ctx: ExecutionContext[dict[str, Any]]):
    """A workflow that demonstrates cancellation."""
    if not ctx.input:
        iterations = 10
    else:
        iterations = ctx.input.get("iterations", 10)

    print(f"[WORKFLOW] Starting cancellable workflow with {iterations} iterations")

    try:
        result = await long_running_task(iterations)
        print(f"[WORKFLOW] Completed successfully with result: {result}")
        return result
    except asyncio.CancelledError:
        print("[WORKFLOW] Caught CancelledError - workflow was cancelled")
        # Let the workflow decorator handle the cancellation
        raise


async def run_cancellation_example():
    """Start the workflow, cancel it mid-flight, inspect the final state."""
    print("Starting cancellation example...\n")

    ctx = ExecutionContext(
        workflow_id="cancellable_workflow",
        workflow_namespace="default",
        workflow_name="cancellable_workflow",
        input={"iterations": 5},
    )

    # Run the workflow as an asyncio task so it can be cancelled — the same
    # thing a worker does when the server relays a cancellation request.
    workflow_task = asyncio.create_task(cancellable_workflow(ctx))

    # Let a few iterations run.
    await asyncio.sleep(3)

    print("\n[CLIENT] Requesting workflow cancellation...\n")
    workflow_task.cancel()

    try:
        await workflow_task
        print("[CLIENT] Workflow completed normally (unexpected)")
    except asyncio.CancelledError:
        print("[CLIENT] Workflow was cancelled as expected")

    print(f"\n[SUMMARY] Final workflow state: {ctx.state.value}")
    print(f"[SUMMARY] Is cancelled: {ctx.is_cancelled}")
    print(f"[SUMMARY] Has finished: {ctx.has_finished}")


if __name__ == "__main__":
    asyncio.run(run_cancellation_example())
