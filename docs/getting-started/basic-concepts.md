# Basic Concepts

Flux is built around three core concepts that make workflow orchestration simple and powerful: **Tasks**, **Workflows**, and **Execution Context**. Understanding these fundamentals will help you leverage Flux's full potential for building resilient, stateful applications.

## Tasks

Tasks are the fundamental building blocks of Flux workflows. They are Python functions decorated with `@task` that perform specific operations within a workflow. Tasks can be simple functions or complex operations with sophisticated error handling and retry mechanisms.

### Basic Tasks

The simplest way to create a task is with the `@task` decorator:

```python
from flux import task

@task
async def process_data(data: str) -> str:
    """Process input data and return the result."""
    return data.upper()

@task
async def send_notification(message: str) -> bool:
    """Send a notification message."""
    print(f"Notification: {message}")
    return True
```

### Configurable Tasks

Tasks can be configured with various options for resilience and behavior control:

```python
from flux import task

@task.with_options(
    name="data_processor",              # Custom task name
    retry_max_attempts=3,               # Maximum retry attempts
    retry_delay=1,                      # Initial delay between retries (seconds)
    retry_backoff=2,                    # Backoff multiplier for retries
    timeout=30,                         # Task timeout in seconds
    fallback=fallback_function,         # Fallback function for failures
    rollback=rollback_function,         # Rollback function for cleanup
    secret_requests=["API_KEY"],        # Required secrets
    cache=True,                         # Enable result caching
    metadata=True                       # Enable task metadata access
)
async def complex_task(data: str) -> str:
    """A task with comprehensive configuration."""
    # Task implementation
    return process_complex_data(data)
```

### Task Features

- **Automatic Retry**: Failed tasks can automatically retry with configurable delays and backoff strategies
- **Timeouts**: Tasks can have execution time limits to prevent hanging
- **Fallback Handling**: Alternative functions can be executed when tasks fail
- **Rollback Support**: Cleanup functions can be triggered after failures
- **Caching**: Task results can be cached to avoid redundant computation
- **Secret Access**: Tasks can request access to secrets managed by Flux
- **Metadata Access**: Tasks can access execution metadata for introspection

## Workflows

Workflows are the orchestration layer that combines multiple tasks to achieve complex business logic. They are Python functions decorated with `@workflow` that define the execution flow and coordination between tasks.

### Basic Workflows

A workflow is defined using the `@workflow` decorator and must take an `ExecutionContext` as its first parameter:

```python
from flux import workflow, ExecutionContext

@workflow
async def data_pipeline(ctx: ExecutionContext[str]):
    """A simple data processing pipeline."""
    # Access input data
    raw_data = ctx.input

    # Execute tasks in sequence
    processed_data = await process_data(raw_data)
    result = await send_notification(f"Processed: {processed_data}")

    return {"data": processed_data, "notified": result}
```

### Workflow Configuration

Workflows can be configured with options for resource requirements and behavior:

```python
@workflow.with_options(
    name="advanced_pipeline",           # Custom workflow name
    secret_requests=["DATABASE_URL"],   # Required secrets
    output_storage=custom_storage,      # Custom output storage
    requests=resource_request           # Resource requirements
)
async def advanced_workflow(ctx: ExecutionContext):
    """A workflow with advanced configuration."""
    # Workflow implementation
    pass
```

### Workflow Characteristics

- **Stateful Execution**: Workflows maintain their state throughout execution
- **Pause and Resume**: Workflows can be paused for manual intervention and resumed later
- **Error Recovery**: Built-in error handling and recovery mechanisms
- **Deterministic Replay**: Workflows produce consistent results when replayed
- **Distributed Execution**: Can run locally or across multiple workers
- **Event Tracking**: Complete audit trail of workflow execution

### Workflow Patterns

Flux supports various execution patterns within workflows:

1. **Sequential execution**: Tasks run one after another (default behavior)
2. **Parallel execution**: Multiple tasks run concurrently using `parallel()`
3. **Pipeline processing**: Tasks form a chain where each receives the previous task's output using `pipeline()`
4. **Graph-based execution**: Complex task dependencies and conditional execution paths using `Graph`

```python
from flux.tasks import parallel, pipeline

@workflow
async def pattern_examples(ctx: ExecutionContext):
    # Sequential execution (default)
    step1 = await task_one(ctx.input)
    step2 = await task_two(step1)

    # Parallel execution
    results = await parallel(
        task_three(step2),
        task_four(step2),
        task_five(step2)
    )

    # Pipeline processing
    pipeline_result = await pipeline(
        task_six,
        task_seven,
        task_eight,
        input=step2
    )

    # Graph-based execution with dependencies
    from flux.tasks import Graph

    graph = Graph("dependency_flow")
    graph.add_node("validate", task_nine)
    graph.add_node("process", task_ten)
    graph.add_edge("validate", "process")
    graph.start_with("validate")
    graph.end_with("process")

    graph_result = await graph(step2)

    return {
        "parallel": results,
        "pipeline": pipeline_result,
        "graph": graph_result
    }
```

## Execution Context

The `ExecutionContext` is the central coordination mechanism in Flux. It maintains the state and metadata of a workflow execution, providing access to input data, execution history, and control mechanisms.

### Core Properties

The execution context provides access to key execution information:

```python
@workflow
async def context_example(ctx: ExecutionContext[str]):
    # Execution identification
    execution_id = ctx.execution_id     # Unique execution identifier
    workflow_name = ctx.workflow_name   # Name of the workflow
    current_worker = ctx.current_worker # Worker executing the workflow

    # Data access
    input_data = ctx.input             # Input data provided to workflow
    output_data = ctx.output           # Final output (available after completion)

    # State information
    is_finished = ctx.has_finished     # Whether execution has completed
    has_succeeded = ctx.has_succeeded  # Whether execution succeeded
    has_failed = ctx.has_failed       # Whether execution failed
    is_paused = ctx.is_paused         # Whether execution is paused
    is_cancelled = ctx.is_cancelled   # Whether execution was cancelled

    # Execution history
    events = ctx.events               # List of all execution events

    return f"Processing {input_data} in execution {execution_id}"
```

### Execution States

The execution context tracks the workflow through various states:

- **CREATED**: Workflow has been created but not started
- **SCHEDULED**: Workflow is scheduled for execution
- **CLAIMED**: Workflow has been claimed by a worker
- **RUNNING**: Workflow is actively executing
- **PAUSED**: Workflow is paused and waiting for resumption
- **COMPLETED**: Workflow has completed successfully
- **FAILED**: Workflow has failed with an error
- **CANCELLING**: Workflow is being cancelled
- **CANCELLED**: Workflow has been cancelled

### Event System

The execution context automatically tracks events throughout the workflow lifecycle:

```python
from flux.domain.events import ExecutionEventType

# Workflow events
ExecutionEventType.WORKFLOW_STARTED    # Workflow begins execution
ExecutionEventType.WORKFLOW_COMPLETED  # Workflow completes successfully
ExecutionEventType.WORKFLOW_FAILED     # Workflow fails with error
ExecutionEventType.WORKFLOW_PAUSED     # Workflow is paused
ExecutionEventType.WORKFLOW_RESUMED    # Workflow is resumed
ExecutionEventType.WORKFLOW_CANCELLED  # Workflow is cancelled

# Task events
ExecutionEventType.TASK_STARTED        # Task begins execution
ExecutionEventType.TASK_COMPLETED      # Task completes successfully
ExecutionEventType.TASK_FAILED         # Task fails with error
ExecutionEventType.TASK_RETRY_STARTED  # Task retry begins
ExecutionEventType.TASK_FALLBACK_STARTED # Task fallback begins
ExecutionEventType.TASK_ROLLBACK_STARTED # Task rollback begins
```

### Accessing Events

You can inspect the execution history through the events:

```python
@workflow
async def event_inspection(ctx: ExecutionContext):
    # Access all events
    for event in ctx.events:
        print(f"Event: {event.type} at {event.time}")
        print(f"Source: {event.source_id}")
        print(f"Value: {event.value}")

    # Get specific event types
    completed_tasks = [
        e for e in ctx.events
        if e.type == ExecutionEventType.TASK_COMPLETED
    ]

    return f"Completed {len(completed_tasks)} tasks"
```

## Putting It All Together

Here's a complete example that demonstrates how tasks, workflows, and execution context work together:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import pause

@task.with_options(retry_max_attempts=3, timeout=30)
async def fetch_data(source: str) -> dict:
    """Fetch data from external source with retry logic."""
    # Simulate data fetching
    return {"source": source, "data": "sample_data"}

@task
async def validate_data(data: dict) -> bool:
    """Validate the fetched data."""
    return "data" in data and data["data"] is not None

@task.with_options(fallback=lambda: "No notification sent")
async def send_alert(message: str) -> str:
    """Send alert with fallback handling."""
    return f"Alert sent: {message}"

@workflow
async def data_processing_pipeline(ctx: ExecutionContext[str]):
    """Complete data processing workflow with error handling."""

    # Step 1: Fetch data
    raw_data = await fetch_data(ctx.input)

    # Step 2: Validate data
    is_valid = await validate_data(raw_data)

    if not is_valid:
        await send_alert("Data validation failed")
        return {"status": "failed", "reason": "invalid_data"}

    # Step 3: Pause for manual approval (optional)
    await pause("data_approval")

    # Step 4: Send success notification
    notification_result = await send_alert("Data processed successfully")

    return {
        "status": "completed",
        "data": raw_data,
        "notification": notification_result,
        "execution_id": ctx.execution_id
    }

# Execute the workflow
if __name__ == "__main__":
    # Local execution
    result_ctx = data_processing_pipeline.run("external_api")

    if result_ctx.has_succeeded:
        print(f"Workflow completed: {result_ctx.output}")
    else:
        print(f"Workflow failed: {result_ctx.output}")
```

## Next Steps

Now that you understand the basic concepts, you can:

1. **Explore Task Configuration**: Learn about [task options](task-options.md) for retry policies, timeouts, and error handling
2. **Dive into Workflows**: Understand [workflow patterns](workflows.md) and composition techniques
3. **Understand State Management**: Learn how [execution context](execution-context.md) manages workflow state
4. **Build Your First Workflow**: Follow the [simple workflow tutorial](tutorials/simple-workflow.md)
5. **Add Resilience**: Learn about [error handling](tutorials/error-handling.md) and recovery mechanisms

These concepts form the foundation for building robust, scalable workflow applications with Flux.
