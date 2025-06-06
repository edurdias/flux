# Built-in Tasks

Flux provides a comprehensive set of built-in tasks that cover common workflow operations, from basic utilities to advanced orchestration patterns. These tasks are designed to be deterministic, fault-tolerant, and fully integrated with Flux's execution model.

## Time Operations

### `now()`
Returns the current timestamp as a Python `datetime` object.

```python
from flux import workflow, ExecutionContext
from flux.tasks import now

@workflow
async def timestamp_workflow(ctx: ExecutionContext):
    start_time = await now()
    # ... do some work ...
    end_time = await now()
    return {
        "started_at": start_time,
        "finished_at": end_time,
        "duration": end_time - start_time
    }
```

### `sleep(duration)`
Pauses workflow execution for a specified duration.

```python
from flux.tasks import sleep
from datetime import timedelta

@workflow
async def delayed_workflow(ctx: ExecutionContext):
    await sleep(2.5)                      # Sleep for 2.5 seconds
    await sleep(timedelta(minutes=5))     # Sleep for 5 minutes
    return "Delayed execution complete"
```

**Parameters:**
- `duration`: Can be either:
  - `float`: Duration in seconds
  - `timedelta`: Python timedelta object

## Random Operations

All random operations in Flux are **deterministic** during workflow replay, ensuring consistent behavior across executions and resumptions.

### `choice(options)`
Randomly selects one item from a list of options.

```python
from flux.tasks import choice

@workflow
async def selection_workflow(ctx: ExecutionContext):
    selected_option = await choice(['A', 'B', 'C', 'D'])
    selected_color = await choice(['red', 'green', 'blue'])
    return f"Selected {selected_option} in {selected_color}"
```

### `randint(a, b)`
Returns a random integer between `a` and `b` (inclusive).

```python
from flux.tasks import randint

@workflow
async def random_number_workflow(ctx: ExecutionContext):
    score = await randint(1, 100)
    dice_roll = await randint(1, 6)
    return {"score": score, "dice": dice_roll}
```

### `randrange(start, stop, step)`
Returns a random number from a range, similar to Python's built-in `random.randrange()`.

```python
from flux.tasks import randrange

@workflow
async def random_range_workflow(ctx: ExecutionContext):
    even_number = await randrange(0, 10, 2)  # Random even number 0-8
    multiple_of_5 = await randrange(0, 100, 5)  # 0, 5, 10, 15, ...
    return {"even": even_number, "multiple_of_5": multiple_of_5}
```

## UUID Generation

### `uuid4()`
Generates a unique UUID (Universally Unique Identifier) using UUID version 4.

```python
from flux.tasks import uuid4

@workflow
async def id_generation_workflow(ctx: ExecutionContext):
    request_id = await uuid4()
    session_id = await uuid4()
    return {
        "request_id": str(request_id),
        "session_id": str(session_id)
    }
```

Like other random operations, UUID generation is deterministic during replay.

## Workflow Control

### `pause(name)`
Creates a named pause point in your workflow, allowing for manual intervention or approval processes.

```python
from flux.tasks import pause

@workflow
async def approval_workflow(ctx: ExecutionContext):
    # Process initial data
    data = await process_data(ctx.input)

    # Pause for manual approval
    await pause("manual_approval")

    # Continue after resume
    final_result = await finalize_data(data)
    return final_result

# First execution - runs until pause
ctx = approval_workflow.run(input_data)

# Resume execution from pause point
ctx = approval_workflow.run(execution_id=ctx.execution_id)
```

**Key Features:**
- Named pause points for identification
- Workflow state is preserved during pause
- Can be resumed from the exact pause point
- Useful for human-in-the-loop workflows

## Orchestration Tasks

### `parallel(*functions)`
Executes multiple tasks concurrently, returning results in the order tasks were defined.

```python
from flux.tasks import parallel

@workflow
async def concurrent_workflow(ctx: ExecutionContext):
    results = await parallel(
        fetch_data_from_api(),
        process_local_file(),
        validate_input(ctx.input)
    )
    return {
        "api_data": results[0],
        "file_data": results[1],
        "validation": results[2]
    }
```

**Key Features:**
- Executes tasks concurrently using asyncio
- Returns results in order of task definition
- Handles individual task failures
- Automatically manages concurrency

### `pipeline(*tasks, input)`
Chains tasks sequentially, passing the output of each task as input to the next.

```python
from flux.tasks import pipeline

@workflow
async def data_pipeline_workflow(ctx: ExecutionContext):
    result = await pipeline(
        normalize_data,
        validate_data,
        transform_data,
        save_data,
        input=ctx.input
    )
    return result
```

**Key Features:**
- Sequential task execution
- Automatic result passing between tasks
- Clear data transformation flow
- Error propagation through the pipeline

### `call(workflow, *args)`
Calls another workflow, either directly or via HTTP API.

```python
from flux.tasks import call

@workflow
async def orchestrator_workflow(ctx: ExecutionContext):
    # Call workflow directly (if available)
    result1 = await call(data_processing_workflow, ctx.input)

    # Call workflow by name via HTTP API
    result2 = await call("external_workflow", processed_data)

    return combine_results(result1, result2)
```

**Key Features:**
- Direct workflow calling for local workflows
- HTTP API calling for remote workflows
- Automatic error handling and propagation
- Supports both sync and async execution modes

## Graph-Based Workflows

### `Graph`
Creates complex task dependencies using directed acyclic graphs (DAGs) with conditional execution paths.

```python
from flux.tasks import Graph

@workflow
async def conditional_workflow(ctx: ExecutionContext):
    workflow_graph = (
        Graph("data_processing")
        # Add nodes (tasks)
        .add_node("validate", validate_input)
        .add_node("process", process_data)
        .add_node("save", save_results)
        .add_node("notify", send_notification)
        .add_node("error", handle_error)

        # Define dependencies and conditions
        .add_edge("validate", "process",
                 condition=lambda result: result.get("valid"))
        .add_edge("validate", "error",
                 condition=lambda result: not result.get("valid"))
        .add_edge("process", "save")
        .add_edge("save", "notify")

        # Define entry and exit points
        .start_with("validate")
        .end_with("notify")
        .end_with("error")
    )

    return await workflow_graph(ctx.input)
```

**Key Features:**
- Define complex task dependencies
- Conditional execution paths
- Automatic validation of graph structure
- Support for multiple entry and exit points

## Task Mapping

### `.map(inputs)`
Apply a task to multiple inputs in parallel (available on any `@task` decorated function).

```python
from flux import task

@task
async def process_item(item: str):
    return item.upper()

@workflow
async def batch_processing_workflow(ctx: ExecutionContext):
    # Process multiple items in parallel
    items = ["hello", "world", "flux", "workflow"]
    results = await process_item.map(items)
    return results  # ["HELLO", "WORLD", "FLUX", "WORKFLOW"]
```

**Key Features:**
- Parallel processing of multiple inputs
- Automatic result aggregation
- Works with any user-defined task
- Efficient resource utilization

## Deterministic Behavior

All built-in tasks maintain **deterministic behavior** during workflow replay:

```python
@workflow
async def deterministic_workflow(ctx: ExecutionContext):
    start = await now()
    random_choice = await choice(['A', 'B', 'C'])
    unique_id = await uuid4()
    random_number = await randint(1, 100)
    end = await now()

    return {
        "start": start,
        "choice": random_choice,
        "id": str(unique_id),
        "number": random_number,
        "duration": end - start
    }

# Original execution
ctx1 = deterministic_workflow.run()

# Replay produces identical results
ctx2 = deterministic_workflow.run(execution_id=ctx1.execution_id)
# ctx1.output == ctx2.output (guaranteed)
```

This deterministic behavior ensures:
- Consistent workflow behavior across executions
- Reliable testing and debugging
- Predictable state during workflow resumption
- Idempotent workflow operations

## Error Handling

Built-in tasks integrate seamlessly with Flux's error handling mechanisms:

```python
from flux.tasks import parallel, pipeline

@workflow
async def resilient_workflow(ctx: ExecutionContext):
    try:
        # Parallel tasks with individual error handling
        results = await parallel(
            safe_task_1(),
            safe_task_2(),
            safe_task_3()
        )

        # Pipeline with error propagation
        final_result = await pipeline(
            validate_results,
            transform_results,
            save_results,
            input=results
        )

        return final_result

    except Exception as e:
        await handle_workflow_error(e)
        raise
```

## Performance Considerations

### Parallel Execution
- Uses asyncio for concurrent task execution
- Best for I/O-bound operations (network requests, file operations)
- Consider resource constraints when grouping tasks

### Pipeline Processing
- Sequential execution, performance depends on slowest task
- Optimize by ordering tasks efficiently
- Group related operations to reduce overhead

### Task Mapping
- Automatically parallelizes operations
- Consider memory usage with large input collections
- Use batching for very large datasets

## Best Practices

1. **Use descriptive pause names** for easier workflow management:
   ```python
   await pause("wait_for_user_approval")
   await pause("manual_quality_check")
   ```

2. **Combine built-in tasks effectively**:
   ```python
   # Generate unique IDs for parallel processing
   task_ids = await parallel(
       uuid4(),
       uuid4(),
       uuid4()
   )
   ```

3. **Leverage deterministic behavior** for testing:
   ```python
   # Tests can rely on consistent random values
   assert await choice(['A', 'B']) == 'A'  # Always true in replay
   ```

4. **Use appropriate orchestration patterns**:
   - `parallel()` for independent tasks
   - `pipeline()` for sequential data transformation
   - `Graph` for complex dependencies
   - `.map()` for batch processing

## Next Steps

- Learn about [Task Definition and Decoration](../user-guide/task-definition.md) to create custom tasks
- Explore [Workflow Patterns](../user-guide/workflow-patterns.md) for advanced composition techniques
- Check out [Error Management](../user-guide/error-management.md) for robust error handling strategies
