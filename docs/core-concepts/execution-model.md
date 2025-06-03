# Execution Model

## Local Execution

Local execution runs workflows directly in your Python application.

### Direct Python Execution
```python
from flux import workflow, ExecutionContext

@workflow
async def my_workflow(ctx: ExecutionContext[str]):
    result = await some_task(ctx.input)
    return result

# Execute the workflow
ctx = my_workflow.run("input_data")

# Access results
print(ctx.output)
print(ctx.succeeded)
```

### Command Line Execution
The `flux` CLI provides workflow execution through workflow registration and execution:

```bash
# First, start the server
flux start server

# Register workflows from a file
flux workflow register workflow_file.py

# Execute the workflow
flux workflow run workflow_name "input_data"

# Example with hello_world workflow
flux workflow run hello_world "World"
```

## API-based Execution

Flux provides a built-in HTTP API server for remote workflow execution.

### Starting the API Server
```bash
# Start the server
flux start server

# Server runs on localhost:8000 by default
```

### Registering Workflows
```bash
# Register workflows from a file
curl -X POST 'http://localhost:8000/workflows' \
     -F 'file=@workflow_file.py'
```

### Making API Requests

```bash
# Execute a workflow (async mode)
curl -X POST 'http://localhost:8000/workflows/hello_world/run/async' \
     --header 'Content-Type: application/json' \
     --data '"World"'

# Execute a workflow (sync mode)
curl -X POST 'http://localhost:8000/workflows/hello_world/run/sync' \
     --header 'Content-Type: application/json' \
     --data '"World"'

# Get execution status
curl -X GET 'http://localhost:8000/workflows/hello_world/status/[execution_id]'
```

Available endpoints:
- `POST /{workflow_name}` - Execute a workflow
- `POST /{workflow_name}/{execution_id}` - Resume a workflow
- `GET /inspect/{execution_id}` - Get execution details

### HTTP API Response Format
```json
{
    "execution_id": "unique_execution_id",
    "name": "workflow_name",
    "input": "input_data",
    "output": "result_data"
}
```

Add `?inspect=true` to get detailed execution information including events:
```bash
curl --location 'localhost:8000/hello_world?inspect=true' \
     --header 'Content-Type: application/json' \
     --data '"World"'
```

## Execution Context

The execution context maintains the state and progression of workflow execution:

```python
# Create execution context
ctx = my_workflow.run("input_data")

# Execution identification
execution_id = ctx.execution_id  # Unique identifier
workflow_name = ctx.name        # Workflow name

# Execution state
is_finished = ctx.finished     # Execution completed
has_succeeded = ctx.succeeded  # Execution succeeded
has_failed = ctx.failed       # Execution failed
is_paused = ctx.paused       # Execution paused

# Data access
input_data = ctx.input        # Input data
output_data = ctx.output      # Output/result data
event_list = ctx.events       # Execution events
```

## Paused Workflows

Flux supports pausing and resuming workflows:

```python
from flux import workflow, ExecutionContext
from flux.tasks import pause

@workflow
async def pausable_workflow(ctx: ExecutionContext):
    # Run until the pause point
    result = await initial_task()

    # Pause execution
    await pause("approval_required")

    # This code runs only after resuming
    return await final_task(result)

# Start execution (runs until pause point)
ctx = pausable_workflow.run()
print(f"Paused: {ctx.paused}")  # True

# Resume execution with the same execution_id
ctx = pausable_workflow.run(execution_id=ctx.execution_id)
print(f"Completed: {ctx.finished}")  # True
```

### Resuming Execution

```python
# Start workflow
ctx = pausable_workflow.run()

# Resume using execution ID
ctx = pausable_workflow.run(execution_id=ctx.execution_id)
```

## State Management

Flux automatically manages workflow state using SQLite for persistence. The state includes:

- Execution context
- Task results
- Events
- Execution status

State is automatically:
- Persisted after each step
- Loaded when resuming execution
- Used for workflow replay
- Managed for error recovery

## Event System

Events track the progression of workflow execution:

### Workflow Events
```python
from flux.events import ExecutionEventType

# Main workflow lifecycle
ExecutionEventType.WORKFLOW_STARTED    # Workflow begins
ExecutionEventType.WORKFLOW_COMPLETED  # Workflow succeeds
ExecutionEventType.WORKFLOW_FAILED     # Workflow fails
```

### Task Events
```python
# Task lifecycle
ExecutionEventType.TASK_STARTED        # Task begins
ExecutionEventType.TASK_COMPLETED      # Task succeeds
ExecutionEventType.TASK_FAILED         # Task fails

# Task retry events
ExecutionEventType.TASK_RETRY_STARTED
ExecutionEventType.TASK_RETRY_COMPLETED
ExecutionEventType.TASK_RETRY_FAILED

# Task fallback events
ExecutionEventType.TASK_FALLBACK_STARTED
ExecutionEventType.TASK_FALLBACK_COMPLETED
ExecutionEventType.TASK_FALLBACK_FAILED

# Task rollback events
ExecutionEventType.TASK_ROLLBACK_STARTED
ExecutionEventType.TASK_ROLLBACK_COMPLETED
ExecutionEventType.TASK_ROLLBACK_FAILED
```

### Accessing Events
```python
# Get all events
for event in ctx.events:
    print(f"Event: {event.type}")
    print(f"Time: {event.time}")
    print(f"Value: {event.value}")

# Get last event
last_event = ctx.events[-1]
```
