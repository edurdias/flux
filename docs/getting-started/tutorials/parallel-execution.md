# Parallel Execution

One of Flux's powerful features is the ability to execute tasks in parallel, significantly improving workflow performance when tasks are independent. This tutorial will show you how to leverage parallel execution to build faster, more efficient workflows.

## What You'll Learn

By the end of this tutorial, you'll understand:
- How to execute tasks in parallel using the `parallel` built-in task
- When to use parallel execution vs sequential execution
- How to handle results from parallel tasks
- Best practices for parallel workflow design

## Prerequisites

- Complete the [Simple Workflow](simple-workflow.md) tutorial
- Understanding of async/await in Python
- Basic knowledge of concurrency concepts

## Understanding Parallel Execution

In sequential execution, tasks run one after another:
```
Task A → Task B → Task C (Total time: A + B + C)
```

In parallel execution, independent tasks run simultaneously:
```
Task A ↘
Task B → Combined Result (Total time: max(A, B, C))
Task C ↗
```

## Basic Parallel Execution

Here's a simple example of running tasks in parallel:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import parallel
import asyncio

@task
async def fetch_user_data(user_id: str):
    """Simulate fetching user data."""
    await asyncio.sleep(2)  # Simulate API delay
    return {
        "user_id": user_id,
        "name": f"User {user_id}",
        "email": f"user{user_id}@example.com"
    }

@task
async def fetch_user_preferences(user_id: str):
    """Simulate fetching user preferences."""
    await asyncio.sleep(1.5)  # Simulate API delay
    return {
        "user_id": user_id,
        "theme": "dark",
        "notifications": True
    }

@task
async def fetch_user_activity(user_id: str):
    """Simulate fetching user activity."""
    await asyncio.sleep(1)  # Simulate API delay
    return {
        "user_id": user_id,
        "last_login": "2025-06-04",
        "total_sessions": 42
    }

@workflow
async def get_complete_user_profile(ctx: ExecutionContext[str]):
    """Fetch all user data in parallel."""
    user_id = ctx.input

    # Execute all three tasks in parallel
    results = await parallel(
        fetch_user_data(user_id),
        fetch_user_preferences(user_id),
        fetch_user_activity(user_id)
    )

    user_data, preferences, activity = results

    # Combine all results
    return {
        "profile": user_data,
        "preferences": preferences,
        "activity": activity
    }

# Example usage
if __name__ == "__main__":
    import time

    start_time = time.time()
    result = get_complete_user_profile.run("123")
    end_time = time.time()

    print(f"Execution time: {end_time - start_time:.2f} seconds")
    print(f"Result: {result.output}")
```

This example will complete in about 2 seconds (the longest task) instead of 4.5 seconds (sum of all tasks).

## Working with Different Task Types

You can mix different types of operations in parallel execution:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import parallel, now, randint
import httpx
import asyncio

@task
async def get_server_time():
    """Get current server time."""
    return await now()

@task
async def generate_random_number():
    """Generate a random number."""
    return await randint(1, 100)

@task
async def fetch_external_data():
    """Fetch data from external API."""
    # Simulate external API call
    await asyncio.sleep(1)
    return {"external_value": 42}

@task
async def calculate_hash(data: str):
    """Calculate hash of input data."""
    import hashlib
    await asyncio.sleep(0.5)  # Simulate processing time
    return hashlib.md5(data.encode()).hexdigest()

@workflow
async def mixed_parallel_workflow(ctx: ExecutionContext[str]):
    """Execute different types of tasks in parallel."""
    input_data = ctx.input

    # Run diverse tasks in parallel
    results = await parallel(
        get_server_time(),
        generate_random_number(),
        fetch_external_data(),
        calculate_hash(input_data)
    )

    timestamp, random_num, external_data, data_hash = results

    return {
        "timestamp": timestamp,
        "random_number": random_num,
        "external_data": external_data,
        "input_hash": data_hash,
        "input": input_data
    }
```

## Parallel Processing with Collections

When you need to process multiple items, parallel execution can significantly improve performance:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import parallel

@task
async def process_item(item: dict):
    """Process a single item."""
    await asyncio.sleep(1)  # Simulate processing time
    return {
        "id": item["id"],
        "processed_value": item["value"] * 2,
        "status": "completed"
    }

@task
async def validate_item(item: dict):
    """Validate a single item."""
    await asyncio.sleep(0.5)  # Simulate validation time
    is_valid = item["value"] > 0
    return {
        "id": item["id"],
        "valid": is_valid,
        "validation_time": await now()
    }

@workflow
async def batch_processing_workflow(ctx: ExecutionContext[list]):
    """Process multiple items in parallel."""
    items = ctx.input

    # Process all items in parallel
    processing_tasks = [process_item(item) for item in items]
    validation_tasks = [validate_item(item) for item in items]

    # Execute processing and validation in parallel
    results = await parallel(
        parallel(*processing_tasks),
        parallel(*validation_tasks)
    )

    processed_items, validations = results

    # Combine results
    combined_results = []
    for processed, validation in zip(processed_items, validations):
        combined_results.append({
            **processed,
            "validation": validation
        })

    return {
        "total_items": len(items),
        "results": combined_results
    }

# Example usage
if __name__ == "__main__":
    test_data = [
        {"id": 1, "value": 10},
        {"id": 2, "value": 20},
        {"id": 3, "value": -5},
        {"id": 4, "value": 30}
    ]

    result = batch_processing_workflow.run(test_data)
    print(result.output)
```

## Error Handling in Parallel Execution

When tasks run in parallel, error handling becomes more complex. Here's how to handle failures gracefully:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import parallel

@task.with_options(
    retry_max_attempts=2,
    fallback=lambda x: {"error": f"Failed to process {x}", "value": None}
)
async def potentially_failing_task(value: int):
    """A task that might fail."""
    if value % 3 == 0:  # Simulate failure for multiples of 3
        raise ValueError(f"Cannot process value {value}")

    await asyncio.sleep(1)
    return {"value": value, "result": value * 2}

@task
async def always_succeeding_task(value: int):
    """A task that always succeeds."""
    await asyncio.sleep(0.5)
    return {"value": value, "safe_result": value + 1}

@workflow
async def robust_parallel_workflow(ctx: ExecutionContext[list]):
    """Handle errors in parallel execution."""
    values = ctx.input

    # Create tasks that might fail
    risky_tasks = [potentially_failing_task(value) for value in values]
    safe_tasks = [always_succeeding_task(value) for value in values]

    # Execute both sets of tasks in parallel
    results = await parallel(
        parallel(*risky_tasks),
        parallel(*safe_tasks)
    )

    risky_results, safe_results = results

    # Process results and separate successful from failed
    successful = []
    failed = []

    for risky, safe in zip(risky_results, safe_results):
        combined = {
            "risky": risky,
            "safe": safe
        }

        if risky.get("error"):
            failed.append(combined)
        else:
            successful.append(combined)

    return {
        "total_processed": len(values),
        "successful": successful,
        "failed": failed,
        "success_rate": len(successful) / len(values)
    }
```

## Performance Considerations

### When to Use Parallel Execution

✅ **Good candidates for parallel execution:**
- I/O-bound tasks (API calls, file operations, database queries)
- Independent tasks that don't depend on each other's results
- CPU-intensive tasks that can be parallelized
- Tasks with similar execution times

❌ **Avoid parallel execution for:**
- Tasks with dependencies between them
- Tasks that modify shared state
- Very quick tasks (overhead might outweigh benefits)
- Resource-constrained environments

### Example: Comparing Sequential vs Parallel

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import parallel
import time
import asyncio

@task
async def simulate_api_call(endpoint: str):
    """Simulate an API call with delay."""
    await asyncio.sleep(1)  # Simulate network delay
    return f"Data from {endpoint}"

@workflow
async def sequential_workflow(ctx: ExecutionContext[list]):
    """Execute API calls sequentially."""
    endpoints = ctx.input
    results = []

    for endpoint in endpoints:
        result = await simulate_api_call(endpoint)
        results.append(result)

    return {"results": results, "execution_type": "sequential"}

@workflow
async def parallel_workflow(ctx: ExecutionContext[list]):
    """Execute API calls in parallel."""
    endpoints = ctx.input

    # Create tasks for all endpoints
    tasks = [simulate_api_call(endpoint) for endpoint in endpoints]

    # Execute all tasks in parallel
    results = await parallel(*tasks)

    return {"results": results, "execution_type": "parallel"}

# Performance comparison
if __name__ == "__main__":
    test_endpoints = ["api1", "api2", "api3", "api4", "api5"]

    # Test sequential execution
    start_time = time.time()
    sequential_result = sequential_workflow.run(test_endpoints)
    sequential_time = time.time() - start_time

    # Test parallel execution
    start_time = time.time()
    parallel_result = parallel_workflow.run(test_endpoints)
    parallel_time = time.time() - start_time

    print(f"Sequential execution: {sequential_time:.2f} seconds")
    print(f"Parallel execution: {parallel_time:.2f} seconds")
    print(f"Speed improvement: {sequential_time / parallel_time:.2f}x")
```

## Advanced Parallel Patterns

### Mixed Sequential and Parallel Execution

Sometimes you need a combination of sequential and parallel execution:

```python
@workflow
async def hybrid_workflow(ctx: ExecutionContext[dict]):
    """Combine sequential and parallel execution."""
    config = ctx.input

    # Step 1: Sequential setup (order matters)
    auth_token = await authenticate(config["credentials"])
    session_id = await create_session(auth_token)

    # Step 2: Parallel data fetching (independent operations)
    data_results = await parallel(
        fetch_user_profile(session_id),
        fetch_user_settings(session_id),
        fetch_user_history(session_id)
    )

    profile, settings, history = data_results

    # Step 3: Sequential processing (depends on all data)
    processed_data = await process_combined_data(profile, settings, history)
    final_result = await generate_report(processed_data)

    # Step 4: Parallel cleanup (independent operations)
    await parallel(
        cleanup_session(session_id),
        log_activity(final_result),
        update_cache(final_result)
    )

    return final_result
```

### Dynamic Parallel Execution

You can create parallel tasks dynamically based on runtime conditions:

```python
@workflow
async def dynamic_parallel_workflow(ctx: ExecutionContext[dict]):
    """Create parallel tasks dynamically."""
    config = ctx.input
    task_count = config.get("task_count", 3)

    # Dynamically create tasks based on configuration
    dynamic_tasks = []
    for i in range(task_count):
        task_config = {
            "id": i,
            "delay": config.get("delay", 1),
            "multiplier": config.get("multiplier", 2)
        }
        dynamic_tasks.append(process_dynamic_task(task_config))

    # Execute all dynamic tasks in parallel
    results = await parallel(*dynamic_tasks)

    return {
        "task_count": task_count,
        "results": results
    }
```

## Best Practices

### 1. Design for Independence
- Ensure parallel tasks don't depend on each other
- Avoid shared mutable state
- Use immutable data structures when possible

### 2. Handle Errors Gracefully
- Use fallback handlers for critical tasks
- Implement retry logic for transient failures
- Consider partial success scenarios

### 3. Monitor Resource Usage
- Be aware of memory usage with many parallel tasks
- Consider rate limiting for external API calls
- Monitor CPU and I/O usage

### 4. Test Thoroughly
- Test with different numbers of parallel tasks
- Verify error handling under various failure scenarios
- Measure performance improvements

## Testing Parallel Workflows

```python
import pytest
from unittest.mock import patch
import asyncio

@pytest.mark.asyncio
async def test_parallel_execution_performance():
    """Test that parallel execution is faster than sequential."""
    call_times = []

    @task
    async def timed_task(delay: float):
        start = time.time()
        await asyncio.sleep(delay)
        end = time.time()
        call_times.append((start, end))
        return f"Task completed after {delay}s"

    # Execute tasks in parallel
    start_time = time.time()
    results = await parallel(
        timed_task(1.0),
        timed_task(1.0),
        timed_task(1.0)
    )
    total_time = time.time() - start_time

    # Verify all tasks completed
    assert len(results) == 3
    assert all("Task completed" in result for result in results)

    # Verify parallel execution (should be close to 1 second, not 3)
    assert total_time < 1.5  # Allow some overhead

    # Verify tasks actually ran in parallel (overlapping time ranges)
    starts = [t[0] for t in call_times]
    assert max(starts) - min(starts) < 0.1  # All started within 100ms

@pytest.mark.asyncio
async def test_parallel_error_handling():
    """Test error handling in parallel execution."""
    @task
    async def failing_task():
        raise ValueError("Task failed")

    @task
    async def succeeding_task():
        return "Success"

    # Even if one task fails, others should complete
    with pytest.raises(ValueError):
        await parallel(failing_task(), succeeding_task())
```

## Next Steps

Now that you understand parallel execution in Flux:

1. Explore [Pipeline Processing](pipeline-processing.md) to learn about sequential task chains
2. Review the [User Guide](../../user-guide/workflow-patterns.md) for advanced parallel patterns
3. Check out [Task Mapping and Iteration](../../user-guide/task-mapping.md) for processing collections

## Summary

In this tutorial, you learned how to:

- **Execute Tasks in Parallel**: Use the `parallel` built-in task to run independent tasks concurrently
- **Handle Collections**: Process multiple items simultaneously for better performance
- **Manage Errors**: Implement robust error handling for parallel operations
- **Optimize Performance**: Choose when to use parallel vs sequential execution
- **Design Hybrid Workflows**: Combine sequential and parallel execution patterns

Parallel execution is a powerful tool for improving workflow performance, especially when dealing with I/O-bound operations or independent processing tasks.
