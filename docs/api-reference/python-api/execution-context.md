# Execution Context

This page documents the ExecutionContext class, which provides workflow state management, logging, and coordination capabilities.

## Overview

The ExecutionContext is the primary interface for interacting with the Flux runtime during workflow execution. It provides:

- **State Management**: Store and retrieve workflow state
- **Logging**: Structured logging with context
- **Coordination**: Task synchronization and parallel execution
- **Metadata**: Access to execution metadata and environment
- **Error Handling**: Exception management and recovery

## Basic Usage

```python
from flux import workflow, task, ExecutionContext

@task
async def example_task(data: str, context: ExecutionContext) -> str:
    # Log task start
    context.log_info(f"Processing data: {data}")

    # Store intermediate state
    context.set("processing_start", context.now())

    # Simulate processing
    result = data.upper()

    # Update state
    context.set("last_result", result)
    context.log_info(f"Processing complete: {result}")

    return result

@workflow
async def example_workflow(context: ExecutionContext[str]):
    input_data = context.input
    result = await example_task(input_data, context)

    # Access stored state
    start_time = context.get("processing_start")
    context.log_info(f"Total processing time: {context.now() - start_time}")

    return result
```

## State Management

### set(key, value)

Store a value in the execution context.

```python
@task
async def state_example(context: ExecutionContext):
    # Store simple values
    context.set("counter", 42)
    context.set("user_name", "Alice")
    context.set("config", {"debug": True, "version": "1.0"})

    # Store complex objects
    context.set("user_data", {
        "id": 123,
        "preferences": {"theme": "dark"},
        "last_login": context.now()
    })
```

**Parameters:**
- `key` (str): Unique key for the value
- `value` (Any): Value to store (must be JSON serializable)

### get(key, default=None)

Retrieve a value from the execution context.

```python
@task
async def retrieve_example(context: ExecutionContext):
    # Get stored values
    counter = context.get("counter")  # Returns 42
    user_name = context.get("user_name")  # Returns "Alice"

    # Get with default value
    theme = context.get("theme", "light")  # Returns "light" if not set

    # Get complex objects
    config = context.get("config", {})
    debug_mode = config.get("debug", False)

    return {
        "counter": counter,
        "user": user_name,
        "theme": theme,
        "debug": debug_mode
    }
```

**Parameters:**
- `key` (str): Key to retrieve
- `default` (Any): Default value if key doesn't exist

**Returns:** The stored value or default

### has(key)

Check if a key exists in the execution context.

```python
@task
async def check_example(context: ExecutionContext):
    if context.has("user_authenticated"):
        # User is authenticated, proceed
        return await process_user_data(context)
    else:
        # Redirect to authentication
        return {"error": "Authentication required"}
```

**Parameters:**
- `key` (str): Key to check

**Returns:** `bool` - True if key exists

### delete(key)

Remove a key from the execution context.

```python
@task
async def cleanup_example(context: ExecutionContext):
    # Clean up temporary data
    context.delete("temp_files")
    context.delete("processing_cache")

    # Clean up sensitive data
    if context.has("auth_token"):
        context.delete("auth_token")
```

**Parameters:**
- `key` (str): Key to remove

### update(data)

Update multiple values at once.

```python
@task
async def batch_update_example(context: ExecutionContext):
    # Update multiple values
    context.update({
        "status": "processing",
        "progress": 0.5,
        "updated_at": context.now(),
        "errors": []
    })
```

**Parameters:**
- `data` (dict): Dictionary of key-value pairs to update

### get_all()

Get all stored values as a dictionary.

```python
@task
async def debug_example(context: ExecutionContext):
    # Get all context data for debugging
    all_data = context.get_all()
    context.log_debug(f"Context data: {all_data}")

    return all_data
```

**Returns:** `dict` - All stored key-value pairs

## Logging

### log(level, message, **kwargs)

Write a log message with specified level.

```python
@task
async def logging_example(context: ExecutionContext):
    # Basic logging
    context.log("info", "Task started")
    context.log("warning", "Deprecated API used")
    context.log("error", "Processing failed")

    # Logging with extra context
    context.log("info", "User action",
                user_id=123,
                action="login",
                ip_address="192.168.1.1")
```

**Parameters:**
- `level` (str): Log level ("debug", "info", "warning", "error", "critical")
- `message` (str): Log message
- `**kwargs`: Additional context data

### log_debug(message, **kwargs)

Write a debug-level log message.

```python
@task
async def debug_logging_example(context: ExecutionContext):
    context.log_debug("Starting data validation")
    context.log_debug("Processing item", item_id=42, size=1024)
```

### log_info(message, **kwargs)

Write an info-level log message.

```python
@task
async def info_logging_example(context: ExecutionContext):
    context.log_info("Task completed successfully")
    context.log_info("Processed items", count=100, duration="5.2s")
```

### log_warning(message, **kwargs)

Write a warning-level log message.

```python
@task
async def warning_logging_example(context: ExecutionContext):
    context.log_warning("API rate limit approaching")
    context.log_warning("Deprecated feature used", feature="old_api_v1")
```

### log_error(message, **kwargs)

Write an error-level log message.

```python
@task
async def error_logging_example(context: ExecutionContext):
    try:
        await risky_operation()
    except Exception as e:
        context.log_error("Operation failed",
                         error=str(e),
                         operation="risky_operation")
        raise
```

### log_critical(message, **kwargs)

Write a critical-level log message.

```python
@task
async def critical_logging_example(context: ExecutionContext):
    context.log_critical("System failure detected",
                        component="database",
                        action="shutdown_initiated")
```

## Parallel Execution

### parallel()

Create a parallel execution context manager.

```python
@task
async def parallel_example(context: ExecutionContext[list]):
    items = context.input
    # Process items in parallel using new async pattern
    results = await parallel([
        process_item(item) for item in items
    ])

    return results
```

**Parameters:**
- `max_workers` (int): Maximum number of parallel workers

### submit(func, *args, **kwargs)

Submit a function for parallel execution (used within parallel context).

```python
@task
async def parallel_processing_example(context: ExecutionContext[list]):
    data_chunks = context.input
    # Submit multiple tasks using modern async pattern
    results = await parallel([
        process_chunk(chunk, context) for chunk in data_chunks
    ])

    # Filter out any None results from failed chunks
    return [r for r in results if r is not None]
```

## Metadata and Environment

### execution_id

Get the unique execution identifier.

```python
@task
async def tracking_example(context: ExecutionContext):
    exec_id = context.execution_id
    context.log_info(f"Execution ID: {exec_id}")

    # Use in external API calls for tracking
    api_response = await call_external_api(correlation_id=exec_id)
    return api_response
```

**Returns:** `str` - Unique execution ID

### workflow_name

Get the name of the current workflow.

```python
@task
async def workflow_info_example(context: ExecutionContext):
    workflow = context.workflow_name
    context.log_info(f"Running workflow: {workflow}")

    # Workflow-specific logic
    if workflow == "data_pipeline":
        return await process_data_pipeline()
    elif workflow == "notification_service":
        return await process_notifications()
```

**Returns:** `str` - Workflow name

### task_name

Get the name of the current task.

```python
@task
async def task_info_example(context: ExecutionContext):
    task = context.task_name
    context.log_info(f"Executing task: {task}")

    # Task-specific configuration
    config = await load_task_config(task)
    return await process_with_config(config)
```

**Returns:** `str` - Current task name

### started_at

Get the workflow start timestamp.

```python
@task
async def timing_example(context: ExecutionContext):
    start_time = context.started_at
    current_time = context.now()

    elapsed = current_time - start_time
    context.log_info(f"Workflow running for: {elapsed}")

    return {"elapsed_seconds": elapsed.total_seconds()}
```

**Returns:** `datetime` - Workflow start time

### now()

Get the current timestamp.

```python
@task
async def timestamp_example(context: ExecutionContext):
    current_time = context.now()

    # Store timestamps
    context.set("last_check", current_time)

    # Format for external APIs
    iso_timestamp = current_time.isoformat()

    return {"timestamp": iso_timestamp}
```

**Returns:** `datetime` - Current timestamp

### environment

Get environment information.

```python
@task
def environment_example(context: ExecutionContext):
    env = context.environment

    # Environment-specific logic
    if env.get("stage") == "production":
        return production_processing()
    else:
        return development_processing()

    # Access environment variables
    database_url = env.get("DATABASE_URL")
    api_key = env.get("API_KEY")

    return {"database": database_url, "api_configured": bool(api_key)}
```

**Returns:** `dict` - Environment configuration

## Error Handling

### add_error(error, recoverable=True)

Add an error to the execution context.

```python
@task
def error_handling_example(context: ExecutionContext):
    try:
        risky_operation()
    except ValueError as e:
        # Add recoverable error
        context.add_error(e, recoverable=True)
        return {"status": "partial_success", "error": str(e)}
    except Exception as e:
        # Add non-recoverable error
        context.add_error(e, recoverable=False)
        raise
```

**Parameters:**
- `error` (Exception): The error that occurred
- `recoverable` (bool): Whether the workflow can continue

### get_errors()

Get all errors that occurred during execution.

```python
@task
def error_summary_example(context: ExecutionContext):
    errors = context.get_errors()

    if errors:
        context.log_warning(f"Found {len(errors)} errors during execution")
        for error in errors:
            context.log_warning(f"Error: {error['message']}, Recoverable: {error['recoverable']}")

    return {"error_count": len(errors)}
```

**Returns:** `list` - List of error dictionaries

### has_errors()

Check if any errors have occurred.

```python
@task
def error_check_example(context: ExecutionContext):
    if context.has_errors():
        context.log_warning("Errors detected, switching to safe mode")
        return safe_mode_processing()
    else:
        return normal_processing()
```

**Returns:** `bool` - True if errors exist

## Workflow Control

### should_stop()

Check if the workflow should stop execution.

```python
@task
def graceful_stop_example(context: ExecutionContext):
    for item in large_dataset:
        if context.should_stop():
            context.log_info("Graceful stop requested, saving progress")
            save_progress(processed_items)
            break

        process_item(item)
        processed_items.append(item)
```

**Returns:** `bool` - True if workflow should stop

### request_stop(reason="User requested")

Request workflow to stop gracefully.

```python
@task
def conditional_stop_example(context: ExecutionContext):
    if error_rate > 0.5:
        context.request_stop("High error rate detected")
        return {"status": "stopped", "reason": "high_error_rate"}
```

**Parameters:**
- `reason` (str): Reason for stopping

## Advanced Features

### create_checkpoint(name)

Create a checkpoint for workflow resumption.

```python
@task
def checkpoint_example(context: ExecutionContext):
    # Process phase 1
    phase1_result = process_phase1()
    context.set("phase1_result", phase1_result)

    # Create checkpoint
    context.create_checkpoint("phase1_complete")

    # Process phase 2
    phase2_result = process_phase2(phase1_result)

    return {"phase1": phase1_result, "phase2": phase2_result}
```

**Parameters:**
- `name` (str): Checkpoint name

### wait_for_condition(condition, timeout=300)

Wait for a condition to become true.

```python
@task
def wait_example(context: ExecutionContext):
    # Wait for external resource to be ready
    def resource_ready():
        return check_resource_status() == "ready"

    context.wait_for_condition(resource_ready, timeout=600)

    # Proceed with processing
    return process_with_resource()
```

**Parameters:**
- `condition` (callable): Function that returns boolean
- `timeout` (int): Maximum wait time in seconds

### emit_event(event_type, data)

Emit custom events for monitoring and integration.

```python
@task
def event_example(context: ExecutionContext):
    # Emit progress events
    context.emit_event("progress", {"percent": 25})

    # Emit custom business events
    context.emit_event("user_created", {
        "user_id": 123,
        "timestamp": context.now().isoformat()
    })
```

**Parameters:**
- `event_type` (str): Type of event
- `data` (dict): Event data

## Context Inheritance

Contexts can be inherited in subworkflows:

```python
@workflow
def parent_workflow(context: ExecutionContext):
    # Set data in parent context
    context.set("parent_data", "shared_value")

    # Child workflow inherits context
    result = child_workflow(context)

    # Access data set by child
    child_data = context.get("child_data")

    return {"parent": "data", "child": child_data, "result": result}

@workflow
def child_workflow(context: ExecutionContext):
    # Access parent data
    parent_data = context.get("parent_data")

    # Set data visible to parent
    context.set("child_data", "processed_value")

    return f"Processed: {parent_data}"
```

## Thread Safety

The ExecutionContext is thread-safe for parallel task execution:

```python
@task
def thread_safe_example(items: list, context: ExecutionContext):
    def process_item_safely(item):
        # Each thread can safely access context
        context.log_info(f"Processing item {item}")

        # Thread-safe state updates
        with context.lock:
            counter = context.get("processed_count", 0)
            context.set("processed_count", counter + 1)

        return f"processed_{item}"

    with context.parallel(max_workers=4) as parallel:
        futures = [parallel.submit(process_item_safely, item) for item in items]
        results = [f.result() for f in futures]

    return results
```

## See Also

- [Core Decorators](core-decorators.md) - Task and workflow decorators
- [Built-in Tasks](built-in-tasks.md) - Pre-built task implementations
- [Workflow Execution](workflow-execution.md) - Execution patterns
- [Data Flow](../../user-guide/data-flow.md) - State management patterns
