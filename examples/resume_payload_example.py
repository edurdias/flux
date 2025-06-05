"""
Example workflow demonstrating resume payload functionality.

This example shows how to:
1. Create a workflow that pauses at a specific point
2. Resume the workflow with a payload
3. Access the resume payload in the workflow
"""

from flux import workflow
from flux.domain import ExecutionContext
from flux.tasks import pause


@workflow
async def resume_payload_example(ctx: ExecutionContext):
    """
    A workflow that demonstrates resume payload functionality.

    This workflow:
    1. Starts with some initial processing
    2. Pauses and waits for user input
    3. When resumed with a payload, uses that data to continue processing
    4. Returns the final result
    """
    print("🚀 Starting workflow...")

    # Initial processing
    initial_data = {"step": 1, "message": "Initial processing complete"}
    print(f"📊 Initial data: {initial_data}")

    # Pause the workflow and wait for resume with payload
    print("⏸️  Pausing workflow for user input...")
    resume_data = await pause("waiting_for_user_input")

    # When resumed, use the payload data
    print(f"▶️  Workflow resumed with data: {resume_data}")

    # Process the resume data
    if isinstance(resume_data, dict):
        final_result = {
            "initial": initial_data,
            "resume_payload": resume_data,
            "status": "completed_with_payload",
        }
    else:
        # Fallback if no meaningful payload was provided
        final_result = {
            "initial": initial_data,
            "resume_data": resume_data,
            "status": "completed_without_payload",
        }

    print(f"✅ Final result: {final_result}")
    return final_result


@workflow
async def multi_pause_example(ctx: ExecutionContext):
    """
    An advanced example with multiple pause points that can each receive different payloads.
    """
    print("🚀 Starting multi-pause workflow...")

    results = []

    # First pause point
    print("⏸️  First pause - waiting for configuration...")
    config_data = await pause("configuration_step")
    results.append({"step": "configuration", "data": config_data})
    print(f"📋 Configuration received: {config_data}")

    # Process based on configuration
    if isinstance(config_data, dict) and config_data.get("enable_second_step", True):
        # Second pause point
        print("⏸️  Second pause - waiting for additional parameters...")
        params_data = await pause("parameters_step")
        results.append({"step": "parameters", "data": params_data})
        print(f"⚙️  Parameters received: {params_data}")
    else:
        print("⏭️  Skipping second step based on configuration")

    # Final processing
    final_result = {
        "workflow": "multi_pause_example",
        "steps_completed": results,
        "total_steps": len(results),
    }

    print(f"✅ Multi-pause workflow completed: {final_result}")
    return final_result


if __name__ == "__main__":
    print("Resume Payload Examples")
    print("=" * 50)

    # Example 1: Basic resume payload usage
    print("\n📋 Example 1: Basic Resume Payload Usage")
    print("-" * 40)

    # Run the workflow initially to pause it
    print("Running workflow to pause point...")
    ctx1 = resume_payload_example.run("initial_input")
    print(f"Workflow paused. Execution ID: {ctx1.execution_id}")
    print(f"Is paused: {ctx1.is_paused}")

    # Resume with a payload
    print("\nResuming with payload...")
    payload = {
        "user_choice": "option_a",
        "additional_data": [1, 2, 3],
        "timestamp": "2024-01-15T10:30:00Z",
    }

    ctx1_resumed = resume_payload_example.run(
        execution_id=ctx1.execution_id,
        resume_payload=payload,
    )
    print(f"Workflow completed: {ctx1_resumed.has_succeeded}")
    print(f"Final output: {ctx1_resumed.output}")

    print("\n" + "=" * 50)

    # Example 2: Multi-pause workflow
    print("\n📋 Example 2: Multi-Pause Workflow")
    print("-" * 40)

    # Start the multi-pause workflow
    print("Starting multi-pause workflow...")
    ctx2 = multi_pause_example.run("multi_input")
    print(f"First pause reached. Execution ID: {ctx2.execution_id}")

    # Resume with configuration
    config_payload = {"enable_second_step": True, "max_retries": 3, "timeout": 30}

    print("Resuming with configuration...")
    ctx2 = multi_pause_example.run(execution_id=ctx2.execution_id, resume_payload=config_payload)
    print(f"Second pause reached: {ctx2.is_paused}")

    # Resume with parameters
    params_payload = {"batch_size": 100, "format": "json", "compression": True}

    print("Resuming with parameters...")
    ctx2_final = multi_pause_example.run(
        execution_id=ctx2.execution_id,
        resume_payload=params_payload,
    )
    print(f"Multi-pause workflow completed: {ctx2_final.has_succeeded}")
    print(f"Final output: {ctx2_final.output}")
