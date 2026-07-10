from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.tasks import pause
from flux.workflow import workflow


@task
async def initial_task():
    """Initial processing before pause."""
    return {"stage": "initial", "data": [1, 2, 3]}


@task
async def process_with_input(initial_data, user_input):
    """Process data with user input received during resume."""
    # Handle None input gracefully
    multiplier = 1
    if user_input and isinstance(user_input, dict):
        multiplier = user_input.get("multiplier", 1)

    return {
        "stage": "processed",
        "initial_data": initial_data,
        "user_input": user_input,
        "result": sum(initial_data["data"]) + multiplier,
    }


@workflow
async def pause_with_input_workflow(ctx: ExecutionContext):
    """
    A workflow that pauses and expects input when resumed.

    The workflow:
    1. Performs initial processing
    2. Pauses and waits for user input
    3. Processes the initial data with the user input
    4. Returns the final result
    """
    # Initial processing
    initial_result = await initial_task()

    # Pause and wait for user input
    # When resumed, the pause task will return the input provided during resume
    user_input = await pause("waiting_for_user_input")

    # Process the data with the user input
    final_result = await process_with_input(initial_result, user_input)

    return final_result


@workflow
async def multiple_input_pause_workflow(ctx: ExecutionContext):
    """
    A workflow with multiple pause points that collect different inputs.
    """
    result: dict = {"stage": "start", "inputs": []}

    # First pause - collect configuration
    config_input = await pause("collect_config")
    result["inputs"].append({"step": 1, "type": "config", "value": config_input})

    # Second pause - collect parameters
    param_input = await pause("collect_parameters")
    result["inputs"].append({"step": 2, "type": "parameters", "value": param_input})

    # Third pause - collect final approval
    approval_input = await pause("final_approval")
    result["inputs"].append({"step": 3, "type": "approval", "value": approval_input})

    result["stage"] = "completed"
    return result


@workflow
async def conditional_pause_workflow(ctx: ExecutionContext):
    """
    A workflow that conditionally pauses based on input.
    """
    # Check if we need approval based on initial input
    if ctx.input and ctx.input.get("requires_approval", False):
        approval = await pause("approval_required")
        return {"approved": approval.get("approved", False), "message": "Approval processed"}
    else:
        return {"approved": True, "message": "No approval required"}


if __name__ == "__main__":  # pragma: no cover
    # Example 1: Basic pause with input
    print("=== Basic Pause with Input ===")
    ctx = pause_with_input_workflow.run()
    print(f"Workflow paused at: {ctx.events[-1].value}")
    print(f"Execution ID: {ctx.execution_id}")
    print("Workflow is paused, waiting for input...")

    # Provide input on resume — workflow.resume accepts it directly and the
    # pause(...) call inside the workflow returns it. (Across processes, use
    # FluxClient.resume_execution_sync or `flux workflow resume`.)
    user_input = {"multiplier": 5, "comment": "Adding multiplier value"}
    print(f"Providing input: {user_input}")
    resumed_ctx = pause_with_input_workflow.resume(ctx.execution_id, user_input)
    print(f"Workflow completed! Output: {resumed_ctx.output}")

    # Example 2: Multiple input pauses
    print("\n=== Multiple Input Pauses ===")
    ctx = multiple_input_pause_workflow.run()
    print(f"First pause at: {ctx.events[-1].value}")

    # Resume with config input
    config_input = {"database_url": "localhost:5432", "timeout": 30}
    ctx = multiple_input_pause_workflow.resume(ctx.execution_id, config_input)
    print(f"Second pause at: {ctx.events[-1].value}")

    # Resume with parameter input
    param_input = {"batch_size": 100, "parallel_workers": 4}
    ctx = multiple_input_pause_workflow.resume(ctx.execution_id, param_input)
    print(f"Third pause at: {ctx.events[-1].value}")

    # Resume with approval input
    approval_input = {"approved": True, "approver": "admin"}
    final_ctx = multiple_input_pause_workflow.resume(ctx.execution_id, approval_input)
    print(f"Final result: {final_ctx.output}")

    # Example 3: Conditional pause
    print("\n=== Conditional Pause (no approval needed) ===")
    ctx = conditional_pause_workflow.run({"requires_approval": False})
    print(f"Result: {ctx.output}")

    print("\n=== Conditional Pause (approval needed) ===")
    ctx = conditional_pause_workflow.run({"requires_approval": True})
    print(f"Paused at: {ctx.events[-1].value}")

    # Resume with approval
    approval = {"approved": True, "reason": "Looks good to proceed"}
    approved_ctx = conditional_pause_workflow.resume(ctx.execution_id, approval)
    print(f"Approval result: {approved_ctx.output}")

    # Example 4: Rejection scenario
    print("\n=== Conditional Pause (approval rejected) ===")
    ctx = conditional_pause_workflow.run({"requires_approval": True})

    # Resume with rejection
    rejection = {"approved": False, "reason": "Needs more review"}
    rejected_ctx = conditional_pause_workflow.resume(ctx.execution_id, rejection)
    print(f"Rejection result: {rejected_ctx.output}")
