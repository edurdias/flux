# Execution Model

## Local Execution

Local execution runs workflows directly in your Python application.

### Direct Python Execution
```python
from flux import workflow, WorkflowExecutionContext

@workflow
async def my_workflow(ctx: WorkflowExecutionContext[str]):
    result = await some_task(ctx.input)
    return result

# Execute the workflow
ctx = my_workflow.run("input_data")

# Access results
print(ctx.output)
print(ctx.succeeded)
```

### Command Line Execution
The `flux` CLI provides workflow execution through the command line:

```bash
# Basic execution
flux exec workflow_file.py workflow_name "input_data"

# Example with hello_world workflow
flux exec hello_world.py hello_world "World"
```

## API-based Execution

Flux provides a built-in HTTP API server for remote workflow execution.

### Starting the API Server
```bash
# Start the server
flux start examples

# Server runs on localhost:8000 by default
```

### Making API Requests

```bash
# Execute a workflow
curl --location 'localhost:8000/hello_world' \
     --header 'Content-Type: application/json' \
     --data '"World"'

# Get execution details
curl --location 'localhost:8000/inspect/[execution_id]'
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

# Data access
input_data = ctx.input        # Input data
output_data = ctx.output      # Output/result data
event_list = ctx.events       # Execution events
```

### Resuming Execution

```python
# Start workflow
ctx = my_workflow.run("input_data")

# Resume using execution ID
ctx = my_workflow.run(execution_id=ctx.execution_id)
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
