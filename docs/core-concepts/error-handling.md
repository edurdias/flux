# Error Handling & Recovery

## Error Types

Flux provides several specialized error types for different scenarios:

```python
from flux.errors import (
    ExecutionError,           # Base error for execution issues
    RetryError,              # Error during retry operations
    ExecutionTimeoutError,   # Timeout during execution
    ExecutionPaused,         # Workflow pause indication
    WorkflowNotFoundError    # Workflow lookup failures
)
```

## Task-Level Error Handling

### Retry Mechanism
```python
@task.with_options(
    retry_max_attempts=3,    # Maximum retry attempts
    retry_delay=1,          # Initial delay in seconds
    retry_backoff=2         # Multiply delay by this factor each retry
)
def task_with_retries():
    # First attempt fails: wait 1 second
    # Second attempt fails: wait 2 seconds
    # Third attempt fails: task fails
    if random.random() < 0.5:
        raise ValueError("Task failed")
    return "success"
```

### Fallback Strategy
```python
def fallback_handler(input_data):
    return "fallback result"

@task.with_options(
    fallback=fallback_handler,
    retry_max_attempts=3
)
def task_with_fallback(input_data):
    # If all retries fail, fallback_handler is called
    if not validate(input_data):
        raise ValueError("Invalid data")
    return process(input_data)
```

### Rollback Operations
```python
def rollback_handler(input_data):
    # Clean up any partial changes
    cleanup_resources()

@task.with_options(rollback=rollback_handler)
def task_with_rollback(input_data):
    # If task fails, rollback_handler is called
    # before propagating the error
    result = complex_operation(input_data)
    if not verify(result):
        raise ValueError("Verification failed")
    return result
```

### Combining Error Handling Strategies
```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    fallback=fallback_handler,
    rollback=rollback_handler,
    timeout=30
)
def task_with_full_error_handling():
    # 1. Task executes with timeout of 30 seconds
    # 2. On failure, retries up to 3 times
    # 3. If retries fail, rollback is executed
    # 4. Finally, fallback is called
    pass
```

## Workflow-Level Error Handling

### Try-Except Pattern
```python
@workflow
def error_handling_workflow(ctx: WorkflowExecutionContext):
    try:
        result = yield risky_task()
        return result
    except ExecutionError as e:
        # Handle execution-specific errors
        print(f"Execution failed: {e.inner_exception}")
        raise
    except Exception as e:
        # Handle unexpected errors
        print(f"Unexpected error: {e}")
        raise
```

### Timeout Handling
```python
@task.with_options(timeout=30)
def long_running_task():
    # Task will raise ExecutionTimeoutError after 30 seconds
    time.sleep(35)

@workflow
def timeout_workflow(ctx: WorkflowExecutionContext):
    try:
        result = yield long_running_task()
        return result
    except ExecutionTimeoutError as e:
        # Handle timeout specifically
        print(f"Task timed out: {e.timeout}s")
        return fallback_value
```

## Error Recovery

### Automatic State Recovery
Flux automatically maintains execution state, allowing workflows to resume from their last successful state:

```python
# Initial execution that might fail
ctx = my_workflow.run("input_data")

# Resume from last successful state
# No need to track state manually
ctx = my_workflow.run(execution_id=ctx.execution_id)
```

### Error Events
Monitor error-related events to track failure patterns:

```python
def analyze_errors(ctx: WorkflowExecutionContext):
    error_events = [
        event for event in ctx.events
        if event.type in [
            ExecutionEventType.TASK_FAILED,
            ExecutionEventType.TASK_RETRY_FAILED,
            ExecutionEventType.TASK_FALLBACK_FAILED,
            ExecutionEventType.WORKFLOW_FAILED
        ]
    ]
    return error_events
```

## Best Practices

### 1. Layer Your Error Handling
```python
@task.with_options(
    retry_max_attempts=3,
    fallback=fallback_handler
)
def resilient_task():
    pass

@workflow
def resilient_workflow(ctx: WorkflowExecutionContext):
    try:
        # Task has its own error handling
        result = yield resilient_task()
        # Workflow provides additional error boundary
        return result
    except Exception as e:
        # Handle unexpected errors
        return None
```

### 2. Use Appropriate Timeouts
```python
@task.with_options(timeout=30)  # HTTP requests
def api_task(): pass

@task.with_options(timeout=300)  # File processing
def processing_task(): pass

@task.with_options(timeout=3600)  # Long computations
def computation_task(): pass
```

### 3. Implement Proper Cleanup
```python
@task.with_options(
    rollback=cleanup_handler,
    fallback=fallback_handler
)
def safe_task():
    # Cleanup handler ensures resources are released
    # Fallback provides alternative result
    pass
```

### 4. Handle Partial Failures
```python
@workflow
def partial_failure_workflow(ctx: WorkflowExecutionContext):
    results = []
    for item in ctx.input:
        try:
            result = yield process_item(item)
            results.append(result)
        except Exception as e:
            # Log error but continue with other items
            results.append(None)
    return results
```
