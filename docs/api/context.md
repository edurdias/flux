# ExecutionContext API Reference

The `ExecutionContext` class is the central object that tracks workflow execution state, input/output data, and provides access to workflow metadata and secrets.

## Class: `flux.ExecutionContext[T]`

### Constructor

```python
ExecutionContext(
    workflow_id: str,
    workflow_name: str,
    input: T | None = None,
    execution_id: str | None = None,
    state: ExecutionState = ExecutionState.PENDING,
    events: list[ExecutionEvent] = None,
    requests: list[str] = None
)
```

**Parameters:**
- `workflow_id`: Unique identifier for the workflow definition
- `workflow_name`: Human-readable workflow name
- `input`: Input data for the workflow (type T)
- `execution_id`: Unique identifier for this execution instance
- `state`: Current execution state
- `events`: List of execution events
- `requests`: List of resource requests

**Type Parameter:**
- `T`: Type of the input data

## Properties

### Input and Output

#### `input: T | None`
The input data provided to the workflow.

#### `output: Any`
The final output of the workflow (available after completion).

#### `partial_results: dict`
Intermediate results from individual tasks during execution.

### Execution Identifiers

#### `workflow_id: str`
Unique identifier for the workflow definition.

#### `workflow_name: str`
Human-readable name of the workflow.

#### `execution_id: str`
Unique identifier for this specific execution instance.

### State Properties

#### `state: ExecutionState`
Current execution state (PENDING, RUNNING, COMPLETED, FAILED, PAUSED, CANCELLED).

#### `has_started: bool`
Whether the workflow has started execution.

#### `has_finished: bool`
Whether the workflow has completed (successfully or with failure).

#### `has_succeeded: bool`
Whether the workflow completed successfully.

#### `has_failed: bool`
Whether the workflow failed during execution.

#### `is_paused: bool`
Whether the workflow is currently paused.

#### `is_cancelled: bool`
Whether the workflow has been cancelled.

### Events and History

#### `events: list[ExecutionEvent]`
Chronological list of execution events.

#### `error: Exception | None`
The exception that caused failure (if workflow failed).

### Resources and Secrets

#### `secrets: dict[str, str]`
Dictionary of secrets available to the workflow.

#### `requests: list[str]`
List of resource requests for the workflow.

## Methods

### State Management

#### `start(source_id: str) -> None`
Mark the workflow as started.

**Parameters:**
- `source_id`: Identifier of the component starting the workflow

#### `complete(source_id: str, value: Any) -> None`
Mark the workflow as completed with a result.

**Parameters:**
- `source_id`: Identifier of the component completing the workflow
- `value`: The final result value

#### `fail(source_id: str, error: Exception) -> None`
Mark the workflow as failed.

**Parameters:**
- `source_id`: Identifier of the component that failed
- `error`: The exception that caused the failure

#### `pause(source_id: str, name: str) -> None`
Pause the workflow at a named pause point.

**Parameters:**
- `source_id`: Identifier of the component requesting pause
- `name`: Name of the pause point

#### `resume(source_id: str) -> None`
Resume a paused workflow.

**Parameters:**
- `source_id`: Identifier of the component resuming the workflow

#### `cancel() -> None`
Cancel the workflow execution.

### Event Management

#### `add_event(event: ExecutionEvent) -> None`
Add an execution event to the history.

**Parameters:**
- `event`: The execution event to add

### Context Management

#### `set_checkpoint(checkpoint_func: Callable) -> None`
Set a function to be called for checkpointing workflow state.

**Parameters:**
- `checkpoint_func`: Async function that saves the execution context

#### `async checkpoint() -> None`
Save the current execution state (calls the checkpoint function).

### Static Methods

#### `get() -> ExecutionContext`
Get the current execution context (within a workflow or task).

**Returns:**
- `ExecutionContext`: The active execution context

**Raises:**
- `RuntimeError`: If called outside of a workflow/task execution

#### `set(ctx: ExecutionContext) -> Token`
Set the current execution context.

**Parameters:**
- `ctx`: The execution context to set as current

**Returns:**
- `Token`: Token for resetting the context later

#### `reset(token: Token) -> None`
Reset the execution context using a token.

**Parameters:**
- `token`: Token obtained from `set()`

## Serialization

### `to_json() -> str`
Serialize the execution context to JSON string.

**Returns:**
- `str`: JSON representation of the execution context

### `to_dict() -> dict`
Convert the execution context to a dictionary.

**Returns:**
- `dict`: Dictionary representation of the execution context

## Usage Examples

### Basic Usage in Workflows

```python
@workflow
async def example_workflow(ctx: ExecutionContext[str]) -> str:
    # Access input data
    input_data = ctx.input

    # Check execution state
    print(f"Workflow ID: {ctx.workflow_id}")
    print(f"Execution ID: {ctx.execution_id}")

    # Process data
    result = await some_task(input_data)

    return result
```

### Accessing Context in Tasks

```python
@task
async def context_aware_task(data: str) -> dict:
    # Get current execution context
    ctx = ExecutionContext.get()

    return {
        "task_input": data,
        "workflow_id": ctx.workflow_id,
        "execution_id": ctx.execution_id,
        "workflow_input": ctx.input
    }
```

### State Inspection

```python
# After workflow execution
ctx = my_workflow.run("input_data")

# Check execution state
if ctx.has_succeeded:
    print(f"Success: {ctx.output}")
elif ctx.has_failed:
    print(f"Failed: {ctx.error}")
elif ctx.is_paused:
    print(f"Paused - can resume with: {ctx.execution_id}")

# Examine execution history
for event in ctx.events:
    print(f"{event.type}: {event.name} at {event.timestamp}")
```

### Working with Secrets

```python
@workflow.with_options(secret_requests=["API_KEY", "DATABASE_URL"])
async def secure_workflow(ctx: ExecutionContext[dict]) -> dict:
    # Access secrets
    api_key = ctx.secrets["API_KEY"]
    db_url = ctx.secrets["DATABASE_URL"]

    # Use secrets in processing
    result = await api_call(ctx.input, api_key)
    await save_to_database(result, db_url)

    return result
```

### Resuming Paused Workflows

```python
from flux.tasks import pause

@workflow
async def approval_workflow(ctx: ExecutionContext[str]) -> str:
    # Process initial data
    processed = await process_data(ctx.input)

    # Pause for approval
    await pause("manual_approval")

    # Continue after approval
    final_result = await finalize_data(processed)
    return final_result

# First execution (runs until pause)
ctx1 = approval_workflow.run("data")
print(f"Paused: {ctx1.is_paused}")

# Resume execution
ctx2 = approval_workflow.run(execution_id=ctx1.execution_id)
print(f"Final result: {ctx2.output}")
```

### Error Handling with Context

```python
@workflow
async def error_handling_workflow(ctx: ExecutionContext[str]) -> str:
    try:
        result = await risky_task(ctx.input)
        return result
    except Exception as e:
        # Error information is automatically stored in ctx.error
        # when the workflow fails
        raise

# Check for errors
ctx = error_handling_workflow.run("invalid_data")
if ctx.has_failed:
    print(f"Workflow failed: {ctx.error}")

    # Examine events leading to failure
    for event in ctx.events:
        if event.type == ExecutionEventType.TASK_FAILED:
            print(f"Task failed: {event.name}")
```

### Custom Checkpointing

```python
async def custom_checkpoint(ctx: ExecutionContext) -> None:
    # Custom logic to save execution state
    await save_to_custom_storage(ctx.to_dict())

@workflow
async def checkpointed_workflow(ctx: ExecutionContext[str]) -> str:
    # Set custom checkpoint function
    ctx.set_checkpoint(custom_checkpoint)

    # Workflow will automatically checkpoint at key points
    result = await long_running_task(ctx.input)
    return result
```

### Serialization and Persistence

```python
# Serialize execution context
ctx = my_workflow.run("data")
json_data = ctx.to_json()

# Save to file or database
with open("execution_state.json", "w") as f:
    f.write(json_data)

# Later, reconstruct from dictionary
import json
with open("execution_state.json", "r") as f:
    data = json.load(f)

# Use the data to resume or analyze execution
print(f"Previous execution result: {data.get('output')}")
```

## Event Types

The execution context tracks various types of events:

### Workflow Events
- `WORKFLOW_STARTED`: Workflow begins execution
- `WORKFLOW_COMPLETED`: Workflow completes successfully
- `WORKFLOW_FAILED`: Workflow fails with an error
- `WORKFLOW_PAUSED`: Workflow is paused
- `WORKFLOW_RESUMED`: Workflow is resumed from pause
- `WORKFLOW_CANCELLED`: Workflow is cancelled

### Task Events
- `TASK_STARTED`: Task begins execution
- `TASK_COMPLETED`: Task completes successfully
- `TASK_FAILED`: Task fails with an error
- `TASK_PAUSED`: Task is paused
- `TASK_RETRY_STARTED`: Task retry attempt begins
- `TASK_RETRY_COMPLETED`: Task retry succeeds
- `TASK_RETRY_FAILED`: Task retry fails
- `TASK_FALLBACK_STARTED`: Fallback function begins
- `TASK_FALLBACK_COMPLETED`: Fallback function completes
- `TASK_FALLBACK_FAILED`: Fallback function fails
- `TASK_ROLLBACK_STARTED`: Rollback function begins
- `TASK_ROLLBACK_COMPLETED`: Rollback function completes

## Thread Safety

The ExecutionContext uses context variables to ensure thread safety:

```python
import asyncio

async def concurrent_workflows():
    # Each workflow gets its own execution context
    tasks = [
        my_workflow.run("input1"),
        my_workflow.run("input2"),
        my_workflow.run("input3")
    ]

    results = await asyncio.gather(*tasks)
    return results
```

## Integration with External Systems

```python
@workflow
async def integration_workflow(ctx: ExecutionContext[dict]) -> dict:
    # Log execution start to external system
    await log_to_external_system(
        f"Started workflow {ctx.workflow_name} with ID {ctx.execution_id}"
    )

    # Process with external API
    result = await external_api_call(ctx.input)

    # Log completion
    await log_to_external_system(
        f"Completed workflow {ctx.execution_id} with result: {result}"
    )

    return result
```
