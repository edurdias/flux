from __future__ import annotations

from flux import workflow
from flux.context import WorkflowExecutionContext
from flux.decorators import task
from flux.tasks import pause


@task
async def init_process():
    return {"stage": "init", "status": "complete"}


@task
async def load_data(previous_state):
    return {**previous_state, "stage": "data_loaded", "data_size": 1000}


@task
async def process_chunk(state, chunk_id):
    return {
        **state,
        "current_chunk": chunk_id,
        "chunks_processed": state.get("chunks_processed", 0) + 1,
    }


@workflow
async def multi_pause_workflow(ctx: WorkflowExecutionContext):
    """
    A workflow demonstrating multiple pause points:
    1. Initial pause after setup for verification
    2. Second pause after data loading for validation
    3. Pause between processing chunks for monitoring
    4. Final pause before completion for approval
    """
    # Initial state setup
    state = await init_process()

    # First pause: Verify setup is correct before proceeding
    await pause("verify_setup")

    # Load data after initial verification
    state = await load_data(state)

    # Second pause: Validate loaded data
    await pause("validate_data")

    # Process data in chunks with pause between each
    total_chunks = 3
    for chunk_id in range(1, total_chunks + 1):
        state = await process_chunk(state, chunk_id)

        # Only pause if there are more chunks to process
        if chunk_id < total_chunks:
            await pause(f"monitor_progress_{chunk_id}")

    # Final pause for approval before completing
    await pause("final_approval")

    # Complete the workflow
    return {**state, "stage": "complete", "message": "Workflow completed successfully"}


if __name__ == "__main__":  # pragma: no cover
    # Run the workflow to the first pause point
    print("Starting workflow - will pause at first checkpoint")
    ctx = multi_pause_workflow.run()

    # Resume past the first pause point (will stop at the second)
    print("\nResuming from first pause...")
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Resume past the second pause point (will stop at the third)
    print("\nResuming from second pause...")
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Resume past the third pause point (will stop at the fourth)
    print("\nResuming from third pause...")
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Resume past the fourth pause point (will stop at the fifth)
    print("\nResuming from fourth pause...")
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Final resume to complete the workflow
    print("\nResuming from final pause...")
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    print("\nFinal workflow state:")
    print(ctx.to_json())
