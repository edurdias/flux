from __future__ import annotations

import asyncio
from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def quick_task(value: str = "world"):
    """A quick task that runs immediately"""
    return f"Hello, {value}"


@task
async def long_running_task(duration: int = 5):
    """A task that takes some time to complete, simulating a long-running operation"""
    print(f"Starting long task that will run for {duration} seconds...")
    for i in range(duration):
        # This will be cooperative with cancellation
        await asyncio.sleep(1)
        print(f"Long task progress: {i+1}/{duration}")
    print("Long task completed!")
    return f"Completed after {duration} seconds"


@workflow
async def cancellable_workflow(ctx: ExecutionContext):
    """A workflow that can be canceled either before or during execution"""
    # First run a quick task
    result = await quick_task("cancellation demo")
    
    # Then run a long task that might be canceled
    long_result = await long_running_task(10)
    
    # Combine results
    return {
        "quick_result": result,
        "long_result": long_result
    }


if __name__ == "__main__":  # pragma: no cover
    ctx = cancellable_workflow.run()
    print(ctx.to_json())
