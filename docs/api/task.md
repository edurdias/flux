# Task API Reference

The `task` decorator is used to define individual units of work that can be composed into workflows.

## Decorator: `@task`

Basic task decorator:

```python
@task
async def my_task(input_data: str) -> str:
    return input_data.upper()
```

## Decorator: `@task.with_options()`

Advanced task configuration:

```python
@task.with_options(
    name="custom_task",
    retry_max_attempts=3,
    retry_delay=1.0,
    retry_backoff=2.0,
    timeout=30.0,
    fallback=fallback_function,
    rollback=cleanup_function,
    cache=True,
    secret_requests=["API_KEY"]
)
async def configured_task(data: str) -> str:
    # Task implementation
    pass
```

**Configuration Parameters:**

### Basic Options
- `name`: Custom task name (defaults to function name)
- `timeout`: Maximum execution time in seconds
- `cache`: Enable result caching (boolean)
- `secret_requests`: List of required secrets

### Retry Configuration
- `retry_max_attempts`: Maximum number of retry attempts (default: 0)
- `retry_delay`: Initial delay between retries in seconds (default: 1.0)
- `retry_backoff`: Backoff multiplier for retry delays (default: 1.0)

### Error Handling
- `fallback`: Function to call if task fails after all retries
- `rollback`: Function to call for cleanup after failure

## Task Function Signature

Task functions should follow these patterns:

```python
# Basic task
async def task_function(param1: Type1, param2: Type2) -> ReturnType:
    # Task implementation
    return result

# Task with multiple parameters
async def multi_param_task(a: int, b: str, c: dict) -> dict:
    return {"a": a, "b": b, "c": c}

# Task that may raise exceptions
async def risky_task(data: str) -> str:
    if not data:
        raise ValueError("Data cannot be empty")
    return data.upper()
```

## Task Features

### Automatic Retries

```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1.0,
    retry_backoff=2.0  # Exponential backoff
)
async def unreliable_task(data: str) -> str:
    # This task will retry up to 3 times with exponential backoff
    # if it raises an exception
    result = await external_api_call(data)
    return result
```

### Timeout Handling

```python
@task.with_options(timeout=10.0)  # 10-second timeout
async def slow_task(data: str) -> str:
    # This task will be cancelled if it takes longer than 10 seconds
    await asyncio.sleep(15)  # This will timeout
    return data
```

### Fallback Functions

```python
async def primary_task(data: str) -> str:
    # Might fail
    return await risky_operation(data)

async def fallback_task(data: str) -> str:
    # Reliable fallback
    return f"Fallback result for: {data}"

@task.with_options(fallback=fallback_task)
async def task_with_fallback(data: str) -> str:
    return await primary_task(data)
```

### Rollback Functions

```python
async def cleanup_resources():
    # Clean up any allocated resources
    await close_connections()
    await delete_temp_files()

@task.with_options(rollback=cleanup_resources)
async def resource_task(data: str) -> str:
    # Allocate resources
    conn = await create_connection()
    temp_file = await create_temp_file()

    # Process data
    result = await process_with_resources(data, conn, temp_file)
    return result
```

### Result Caching

```python
@task.with_options(cache=True)
async def expensive_computation(n: int) -> int:
    # This result will be cached based on input parameters
    # Subsequent calls with the same parameters return cached result
    result = 0
    for i in range(n):
        result += i * i
    return result
```

### Secret Access

```python
@task.with_options(secret_requests=["DATABASE_URL", "API_KEY"])
async def secure_task(data: str) -> dict:
    # Access secrets through execution context
    ctx = ExecutionContext.get()
    db_url = ctx.secrets["DATABASE_URL"]
    api_key = ctx.secrets["API_KEY"]

    # Use secrets safely
    result = await api_call(data, api_key)
    await save_to_db(result, db_url)
    return result
```

## Task Composition Patterns

### Sequential Execution

```python
@workflow
async def sequential_workflow(ctx: ExecutionContext[str]) -> str:
    step1 = await task1(ctx.input)
    step2 = await task2(step1)
    step3 = await task3(step2)
    return step3
```

### Parallel Execution

```python
from flux.tasks import parallel

@workflow
async def parallel_workflow(ctx: ExecutionContext[str]) -> list:
    results = await parallel(
        task1(ctx.input),
        task2(ctx.input),
        task3(ctx.input)
    )
    return results
```

### Pipeline Processing

```python
from flux.tasks import pipeline

@workflow
async def pipeline_workflow(ctx: ExecutionContext[str]) -> str:
    result = await pipeline(
        task1,
        task2,
        task3,
        input=ctx.input
    )
    return result
```

### Task Mapping

```python
@workflow
async def mapping_workflow(ctx: ExecutionContext[list[str]]) -> list[str]:
    # Apply task to each item in the list
    results = await process_item.map(ctx.input)
    return [result.output for result in results]
```

## Error Handling in Tasks

### Basic Exception Handling

```python
@task
async def safe_task(data: str) -> str:
    try:
        result = await risky_operation(data)
        return result
    except SpecificError as e:
        # Handle specific errors
        return f"Handled error: {e}"
    except Exception as e:
        # Handle general errors
        raise TaskError(f"Task failed: {e}")
```

### Retry with Custom Logic

```python
@task.with_options(retry_max_attempts=3)
async def smart_retry_task(data: str) -> str:
    try:
        return await external_service_call(data)
    except ConnectionError:
        # This will trigger retry
        raise
    except ValidationError as e:
        # This won't retry (permanent error)
        raise TaskError(f"Invalid data: {e}")
```

## Built-in Task Utilities

Tasks can use various built-in utilities:

```python
from flux.tasks import now, sleep, uuid4, choice, randint

@task
async def utility_task() -> dict:
    start_time = await now()
    unique_id = await uuid4()
    random_choice = await choice(['A', 'B', 'C'])
    random_number = await randint(1, 100)

    await sleep(1.0)  # Sleep for 1 second

    end_time = await now()

    return {
        "id": unique_id,
        "choice": random_choice,
        "number": random_number,
        "duration": end_time - start_time
    }
```

## Task Metadata

Access task execution metadata:

```python
@task
async def metadata_task(data: str) -> dict:
    ctx = ExecutionContext.get()

    return {
        "task_id": ctx.current_task_id,
        "workflow_id": ctx.workflow_id,
        "execution_id": ctx.execution_id,
        "input": data,
        "timestamp": await now()
    }
```

## Testing Tasks

Tasks can be tested in isolation:

```python
import pytest
from flux import ExecutionContext

@pytest.mark.asyncio
async def test_my_task():
    # Create test execution context
    ctx = ExecutionContext(
        workflow_id="test_workflow",
        workflow_name="test",
        input="test_data"
    )

    # Set context for task execution
    token = ExecutionContext.set(ctx)
    try:
        # Test the task
        result = await my_task("test_input")
        assert result == "expected_output"
    finally:
        ExecutionContext.reset(token)
```

## Performance Considerations

### Async Best Practices

```python
@task
async def efficient_task(urls: list[str]) -> list[dict]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        # Use asyncio.gather for concurrent requests
        tasks = [fetch_url(session, url) for url in urls]
        results = await asyncio.gather(*tasks)
        return results

async def fetch_url(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url) as response:
        return await response.json()
```

### Resource Management

```python
@task.with_options(rollback=cleanup_resources)
async def resource_efficient_task(data: str) -> str:
    # Acquire resources
    resource = await acquire_expensive_resource()

    try:
        # Use resource
        result = await process_with_resource(data, resource)
        return result
    finally:
        # Ensure cleanup even on success
        await release_resource(resource)
```
