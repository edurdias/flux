# Basic Concepts

## Workflows

A workflow in Flux is a Python function that orchestrates a series of tasks to achieve a specific goal. Workflows are defined using the `@workflow` decorator and provide a high-level way to organize and manage task execution.

```python
from flux import workflow, WorkflowExecutionContext
from flux.tasks import pause

# Basic workflow
@workflow
async def my_workflow(ctx: WorkflowExecutionContext[str]):
    result = await some_task(ctx.input)
    return result

# Configured workflow
@workflow.with_options(
    name="custom_workflow",              # Custom workflow name
    secret_requests=["API_KEY"],         # Required secrets
    output_storage=custom_storage        # Custom output storage
)
async def configured_workflow(ctx: WorkflowExecutionContext):
    result = await some_task()
    return result

# Workflow with pause point
@workflow
async def approval_workflow(ctx: WorkflowExecutionContext):
    data = await process_data(ctx.input)
    # Pause for manual approval
    await pause("manual_approval")
    # Continues after workflow is resumed
    return f"Approved: {data}"
```

Key characteristics of workflows:
- Must be decorated with `@workflow` or `@workflow.with_options()`
- Take a `WorkflowExecutionContext` as their first argument
- Use `async/await` to execute tasks asynchronously
- Can be run directly, via CLI, or through HTTP API
- Maintain execution state between runs
- Support pause and resume operations for manual interventions

## Tasks

Tasks are the basic units of work in Flux. They are Python functions decorated with `@task` that perform specific operations within a workflow.

```python
from flux import task

# Basic task
@task
async def simple_task(data: str):
    return data.upper()

# Configured task
@task.with_options(
    name="custom_task",                  # Custom task name
    retry_max_attempts=3,                 # Maximum retry attempts
    retry_delay=1,                       # Initial delay between retries
    retry_backoff=2,                     # Backoff multiplier for retries
    timeout=30,                          # Task timeout in seconds
    fallback=fallback_function,          # Fallback function for failures
    rollback=rollback_function,          # Rollback function for failures
    secret_requests=["API_KEY"],         # Required secrets
    output_storage=custom_storage        # Custom output storage
)
async def complex_task(data: str):
    return process_data(data)
```

Task features:
- Basic tasks with `@task` decorator
- Configurable options:
  - `retry_max_attempts`: Maximum retry attempts
  - `retry_delay`: Initial delay between retries
  - `retry_backoff`: Backoff multiplier for subsequent retries
  - `timeout`: Task execution timeout
  - `fallback`: Fallback function for handling failures
- Can be composed and nested
- Support for parallel execution and mapping operations

## Execution Context

The `WorkflowExecutionContext` is a container that maintains the state and information about a workflow execution.

```python
from flux import WorkflowExecutionContext

@workflow
async def example_workflow(ctx: WorkflowExecutionContext[str]):
    # Access context properties
    execution_id = ctx.execution_id  # Unique execution identifier
    input_data = ctx.input          # Workflow input
    is_finished = ctx.finished      # Execution status
    has_succeeded = ctx.succeeded   # Success status
    output_data = ctx.output       # Workflow output
```

Context properties:
- `execution_id`: Unique identifier for the workflow execution
- `name`: Name of the workflow
- `input`: Input data provided to the workflow
- `events`: List of execution events
- `finished`: Whether the workflow has completed
- `succeeded`: Whether the workflow completed successfully
- `failed`: Whether the workflow failed
- `paused`: Whether the workflow is currently paused
- `output`: Final output of the workflow

## Events

Events track the progress and state changes during workflow execution. Flux automatically generates events for various workflow and task operations.

```python
from flux.events import ExecutionEventType

# Example of event types
ExecutionEventType.WORKFLOW_STARTED    # Workflow begins execution
ExecutionEventType.WORKFLOW_COMPLETED  # Workflow completes successfully
ExecutionEventType.WORKFLOW_PAUSED    # Workflow is paused
ExecutionEventType.TASK_STARTED       # Task begins execution
ExecutionEventType.TASK_COMPLETED     # Task completes successfully
ExecutionEventType.TASK_PAUSED       # Task is paused
```

Event categories:
1. Workflow Events:
   - `WORKFLOW_STARTED`
   - `WORKFLOW_COMPLETED`
   - `WORKFLOW_FAILED`
   - `WORKFLOW_PAUSED`

2. Task Events:
   - `TASK_STARTED`
   - `TASK_COMPLETED`
   - `TASK_FAILED`
   - `TASK_PAUSED`
   - `TASK_RETRY_STARTED`
   - `TASK_RETRY_COMPLETED`
   - `TASK_RETRY_FAILED`
   - `TASK_FALLBACK_STARTED`
   - `TASK_FALLBACK_COMPLETED`
   - `TASK_FALLBACK_FAILED`
   - `TASK_ROLLBACK_STARTED`
   - `TASK_ROLLBACK_COMPLETED`
   - `TASK_ROLLBACK_FAILED`
