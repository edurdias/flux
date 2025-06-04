# Core Decorators

This page documents the core decorators that form the foundation of Flux workflow development.

## Overview

Flux provides a set of powerful decorators that transform regular Python functions into workflow tasks. These decorators handle execution coordination, state management, error handling, and resource allocation.

## @task Decorator

The `@task` decorator is the primary way to define workflow tasks in Flux.

### Basic Usage

```python
from flux import task

@task
async def hello_world(name: str) -> str:
    """A simple task that greets someone."""
    return f"Hello, {name}!"

# Usage in workflow
result = await hello_world("Alice")
print(result)  # "Hello, Alice!"
```

### Parameters

#### name
**Type**: `str`
**Default**: Function name
**Description**: Custom name for the task

```python
@task(name="custom_greeting")
async def hello_world(name: str) -> str:
    return f"Hello, {name}!"
```

#### description
**Type**: `str`
**Default**: Function docstring
**Description**: Task description for documentation

```python
@task(description="Greets a person by name")
async def hello_world(name: str) -> str:
    return f"Hello, {name}!"
```

#### timeout
**Type**: `int`
**Default**: `300` (5 minutes)
**Description**: Maximum execution time in seconds

```python
@task(timeout=60)  # 1 minute timeout
def quick_task():
    return "Done quickly"

@task(timeout=3600)  # 1 hour timeout
def long_running_task():
    # Simulate long processing
    time.sleep(30)
    return "Completed after long processing"
```

#### retries
**Type**: `int`
**Default**: `0`
**Description**: Number of retry attempts on failure

```python
@task(retries=3)
def unreliable_task():
    if random.random() < 0.7:
        raise Exception("Random failure")
    return "Success!"
```

#### retry_delay
**Type**: `int`
**Default**: `5`
**Description**: Delay between retries in seconds

```python
@task(retries=3, retry_delay=10)
def task_with_backoff():
    # Will retry 3 times with 10 second delays
    pass
```

#### retry_backoff
**Type**: `str`
**Default**: `"linear"`
**Description**: Backoff strategy (`"linear"`, `"exponential"`, `"fixed"`)

```python
@task(retries=3, retry_delay=5, retry_backoff="exponential")
def task_with_exponential_backoff():
    # Retries at 5s, 10s, 20s intervals
    pass
```

#### priority
**Type**: `int`
**Default**: `5`
**Range**: `1-10`
**Description**: Task execution priority (10 = highest)

```python
@task(priority=8)
def high_priority_task():
    return "Important work"

@task(priority=2)
def low_priority_task():
    return "Background work"
```

#### resources
**Type**: `dict`
**Default**: `{}`
**Description**: Resource requirements for task execution

```python
@task(resources={
    "cpu": 2.0,
    "memory": "4Gi",
    "disk": "10Gi",
    "gpu": 1
})
def resource_intensive_task():
    # Requires 2 CPU cores, 4GB RAM, 10GB disk, 1 GPU
    return "Heavy computation done"
```

#### tags
**Type**: `list[str]`
**Default**: `[]`
**Description**: Tags for task categorization

```python
@task(tags=["data-processing", "etl", "production"])
def data_pipeline_task():
    return "Data processed"
```

#### depends_on
**Type**: `list[str]`
**Default**: `[]`
**Description**: Task dependencies

```python
@task(name="setup")
def setup_task():
    return "Setup complete"

@task(depends_on=["setup"])
def main_task():
    return "Main work done"
```

### Complete Example

```python
@task(
    name="process_user_data",
    description="Processes user data with validation and transformation",
    timeout=600,
    retries=2,
    retry_delay=30,
    retry_backoff="exponential",
    priority=7,
    resources={
        "cpu": 1.5,
        "memory": "2Gi"
    },
    tags=["data-processing", "user-management"],
    depends_on=["validate_input"]
)
def process_user_data(user_id: str, context: ExecutionContext) -> dict:
    """Process user data with comprehensive error handling."""
    try:
        # Simulate data processing
        context.log_info(f"Processing user {user_id}")

        # Your processing logic here
        result = {"user_id": user_id, "status": "processed"}

        context.log_info(f"Successfully processed user {user_id}")
        return result

    except Exception as e:
        context.log_error(f"Failed to process user {user_id}: {e}")
        raise
```

## @workflow Decorator

The `@workflow` decorator defines a workflow composed of multiple tasks.

### Basic Usage

```python
from flux import workflow, task, ExecutionContext

@task
async def step_one() -> str:
    return "Step 1 complete"

@task
async def step_two(input_data: str) -> str:
    return f"{input_data} -> Step 2 complete"

@workflow
async def simple_workflow(context: ExecutionContext):
    """A simple two-step workflow."""
    result1 = await step_one()
    result2 = await step_two(result1)
    return result2
```

### Parameters

#### name
**Type**: `str`
**Default**: Function name
**Description**: Custom workflow name

```python
@workflow(name="data_pipeline_v2")
async def my_workflow(context: ExecutionContext):
    pass
```

#### description
**Type**: `str`
**Default**: Function docstring
**Description**: Workflow description

```python
@workflow(description="Processes daily sales data")
async def sales_pipeline(context: ExecutionContext):
    pass
```

#### version
**Type**: `str`
**Default**: `"1.0.0"`
**Description**: Workflow version for tracking

```python
@workflow(version="2.1.0")
def versioned_workflow():
    pass
```

#### timeout
**Type**: `int`
**Default**: `3600` (1 hour)
**Description**: Maximum workflow execution time

```python
@workflow(timeout=7200)  # 2 hours
def long_workflow():
    pass
```

#### max_parallel_tasks
**Type**: `int`
**Default**: `10`
**Description**: Maximum concurrent task execution

```python
@workflow(max_parallel_tasks=5)
def controlled_parallelism():
    pass
```

#### error_strategy
**Type**: `str`
**Default**: `"fail_fast"`
**Description**: How to handle task failures (`"fail_fast"`, `"continue"`, `"skip_dependents"`)

```python
@workflow(error_strategy="continue")
def resilient_workflow():
    # Continue executing other tasks even if some fail
    pass
```

#### tags
**Type**: `list[str]`
**Default**: `[]`
**Description**: Workflow tags

```python
@workflow(tags=["etl", "daily", "production"])
def tagged_workflow():
    pass
```

### Complete Example

```python
@workflow(
    name="comprehensive_data_pipeline",
    description="A comprehensive ETL pipeline for daily data processing",
    version="3.2.1",
    timeout=10800,  # 3 hours
    max_parallel_tasks=8,
    error_strategy="skip_dependents",
    tags=["etl", "production", "scheduled"]
)
def data_pipeline(source_path: str, target_path: str, context: ExecutionContext):
    """
    Comprehensive data processing pipeline.

    Args:
        source_path: Path to source data
        target_path: Path for processed data
        context: Execution context for logging and state
    """
    # Initialize pipeline
    context.log_info("Starting data pipeline")

    # Extract phase
    raw_data = extract_data(source_path)

    # Transform phase (parallel processing)
    with context.parallel() as parallel:
        cleaned_data = parallel.submit(clean_data, raw_data)
        validated_data = parallel.submit(validate_data, cleaned_data)
        enriched_data = parallel.submit(enrich_data, validated_data)

    # Load phase
    load_result = load_data(enriched_data, target_path)

    context.log_info("Data pipeline completed successfully")
    return {
        "status": "completed",
        "records_processed": len(enriched_data),
        "output_path": target_path
    }
```

## @parallel Decorator

The `@parallel` decorator enables parallel execution of tasks.

### Basic Usage

```python
from flux import parallel, task

@task
async def process_chunk(chunk_id: int, data: list) -> dict:
    # Process data chunk
    return {"chunk": chunk_id, "count": len(data)}

async def process_data_parallel(data_chunks: list) -> list:
    """Process multiple data chunks in parallel."""
    results = await parallel([
        process_chunk(i, chunk) for i, chunk in enumerate(data_chunks)
    ])
    return results
```

### Parameters

#### max_workers
**Type**: `int`
**Default**: `None` (CPU count)
**Description**: Maximum number of parallel workers

```python
async def heavy_parallel_work(data_items: list):
    results = await parallel(
        [process_item(item) for item in data_items],
        max_workers=8
    )
    return results
```

#### executor_type
**Type**: `str`
**Default**: `"thread"`
**Description**: Executor type (`"thread"`, `"process"`)

```python
@parallel(executor_type="process", max_workers=4)
def cpu_intensive_parallel():
    # Use process pool for CPU-bound tasks
    pass
```

#### timeout
**Type**: `int`
**Default**: `None`
**Description**: Timeout for parallel execution

```python
@parallel(max_workers=4, timeout=300)
def time_limited_parallel():
    pass
```

## @cache Decorator

The `@cache` decorator provides result caching for tasks.

### Basic Usage

```python
from flux import cache, task

@task
@cache(ttl=3600)  # Cache for 1 hour
def expensive_computation(input_value: str) -> str:
    """Expensive computation that benefits from caching."""
    # Simulate expensive work
    time.sleep(10)
    return f"Processed: {input_value}"
```

### Parameters

#### ttl
**Type**: `int`
**Default**: `3600` (1 hour)
**Description**: Time-to-live in seconds

#### key_func
**Type**: `callable`
**Default**: `None`
**Description**: Custom cache key generation function

```python
def custom_key(func, *args, **kwargs):
    return f"{func.__name__}:{hash(str(args))}"

@task
@cache(ttl=7200, key_func=custom_key)
def cached_task(data: dict) -> str:
    return "Result"
```

#### storage
**Type**: `str`
**Default**: `"memory"`
**Description**: Cache storage backend (`"memory"`, `"redis"`, `"file"`)

```python
@task
@cache(ttl=86400, storage="redis")
def persistent_cached_task() -> str:
    return "Cached in Redis"
```

## @conditional Decorator

The `@conditional` decorator allows conditional task execution.

### Basic Usage

```python
from flux import conditional, task

@task
@conditional(lambda ctx: ctx.get("environment") == "production")
async def production_only_task():
    """Only runs in production environment."""
    return "Production task executed"

@workflow
async def environment_aware_workflow(context: ExecutionContext):
    # Set environment context
    context.set("environment", "production")

    # This task will only run if condition is met
    result = await production_only_task()
    return result
```

### Parameters

#### condition
**Type**: `callable`
**Description**: Function that returns boolean for execution decision

```python
def should_run_backup(context):
    return context.get("day_of_week") == "sunday"

@task
@conditional(should_run_backup)
async def weekly_backup():
    return "Backup completed"
```

#### skip_value
**Type**: `Any`
**Default**: `None`
**Description**: Value to return when condition is false

```python
@task
@conditional(
    condition=lambda ctx: ctx.get("mode") == "full",
    skip_value="Skipped - partial mode"
)
def full_processing_task():
    return "Full processing completed"
```

## Decorator Composition

Decorators can be combined for powerful task configuration:

```python
@task(
    name="comprehensive_task",
    timeout=1800,
    retries=3,
    priority=8
)
@cache(ttl=3600)
@conditional(lambda ctx: ctx.get("enable_caching", True))
@parallel(max_workers=2)
def comprehensive_task(data: list, context: ExecutionContext) -> dict:
    """
    A task demonstrating decorator composition:
    - Cached results for 1 hour
    - Only runs if caching is enabled
    - Processes data in parallel
    - Has retry logic and high priority
    """
    results = []

    with context.parallel() as parallel:
        for item in data:
            result = parallel.submit(process_item, item)
            results.append(result)

    return {
        "processed_count": len(results),
        "results": results
    }
```

## Custom Decorators

You can create custom decorators for specific workflow patterns:

```python
from functools import wraps
from flux import task

def audit_task(audit_level="info"):
    """Custom decorator that adds auditing to tasks."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            context = kwargs.get('context')
            if context:
                context.log(audit_level, f"Starting task: {func.__name__}")

            try:
                result = func(*args, **kwargs)
                if context:
                    context.log(audit_level, f"Completed task: {func.__name__}")
                return result
            except Exception as e:
                if context:
                    context.log("error", f"Failed task: {func.__name__}: {e}")
                raise

        return task(wrapper)
    return decorator

# Usage
@audit_task(audit_level="debug")
def audited_task(data: str, context: ExecutionContext) -> str:
    return f"Processed: {data}"
```

## See Also

- [Built-in Tasks](built-in-tasks.md) - Pre-built task implementations
- [Execution Context](execution-context.md) - Context management and state
- [Workflow Execution](workflow-execution.md) - Execution patterns and control
- [Task Configuration](../../user-guide/task-configuration.md) - Advanced configuration options
