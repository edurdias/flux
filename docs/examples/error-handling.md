# Error Handling Examples

This section demonstrates various error handling patterns and fault tolerance mechanisms in Flux workflows.

## Basic Error Handling

Demonstrates fundamental error handling patterns with try-catch blocks and graceful degradation.

**Key concepts:**
- Exception propagation in workflows
- Graceful error recovery
- Error logging and reporting
- Fallback mechanisms

## Task Timeout Handling

Shows how to handle tasks that exceed time limits.

**File:** `examples/tasks/` (various timeout examples)

```python
from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow
import asyncio

@task(timeout=5.0)  # 5 second timeout
async def potentially_slow_task(data: str) -> str:
    # Simulate work that might take too long
    await asyncio.sleep(10)  # This will timeout
    return f"Processed: {data}"

@task
async def fallback_task(data: str) -> str:
    return f"Fallback result for: {data}"

@workflow
async def timeout_handling_workflow(ctx: ExecutionContext[str]):
    try:
        return await potentially_slow_task(ctx.input)
    except TimeoutError:
        return await fallback_task(ctx.input)
```

**Key concepts demonstrated:**
- Task timeout configuration
- Timeout exception handling
- Fallback task execution
- Resilient workflow patterns

## Retry Mechanisms

Demonstrates automatic retry logic for transient failures.

```python
from flux.task import task
from flux.workflow import workflow

@task(retry_count=3, retry_delay=1.0)
async def unreliable_task(data: str) -> str:
    # Simulate a task that fails randomly
    import random
    if random.random() < 0.7:  # 70% chance of failure
        raise ValueError("Simulated failure")
    return f"Success: {data}"

@workflow
async def retry_workflow(ctx: ExecutionContext[str]):
    return await unreliable_task(ctx.input)
```

**Key concepts demonstrated:**
- Automatic retry configuration
- Retry delay and backoff
- Transient vs permanent failure handling
- Retry exhaustion handling

## Fallback After Retry

Shows how to combine retry mechanisms with fallback strategies.

**File:** `examples/tasks/test_task_fallback_after_retry.py`

**Key concepts demonstrated:**
- Multi-layered error recovery
- Retry exhaustion detection
- Fallback task execution
- Comprehensive error handling strategies

## Fallback After Timeout

Demonstrates fallback mechanisms when tasks exceed time limits.

**File:** `examples/tasks/test_task_fallback_after_timeout.py`

**Key concepts demonstrated:**
- Timeout detection and handling
- Immediate fallback execution
- Resource cleanup after timeout
- Time-sensitive workflow patterns

## Cancellation Handling

Comprehensive cancellation example showing graceful shutdown patterns.

**File:** `examples/cancellation.py`

```python
import asyncio
from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow

@task
async def long_running_task(iterations: int = 10):
    results = []
    try:
        for i in range(iterations):
            await asyncio.sleep(1)
            print(f"Completed iteration {i + 1}/{iterations}")
            results.append(i)
        return results
    except asyncio.CancelledError:
        print("Task was cancelled")
        # Perform cleanup
        raise

@workflow
async def cancellable_workflow(ctx: ExecutionContext[dict]):
    iterations = ctx.input.get("iterations", 10)
    return await long_running_task(iterations)
```

**Key concepts demonstrated:**
- Graceful cancellation handling
- Resource cleanup on cancellation
- Cancellation propagation
- Cooperative cancellation patterns

## Complex Error Scenarios

Real-world error handling patterns for complex workflows.

### Network Failures
```python
@task(retry_count=3, retry_delay=2.0)
async def api_request_task(url: str) -> dict:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                return await response.json()
    except aiohttp.ClientError as e:
        raise ConnectionError(f"Network error: {e}")
```

### Data Validation Errors
```python
@task
async def validate_and_process(data: dict) -> dict:
    required_fields = ["id", "name", "email"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    # Process valid data
    return {"processed": True, "data": data}
```

### Resource Exhaustion
```python
@task
async def memory_intensive_task(size: int) -> list:
    try:
        # Simulate memory-intensive operation
        data = [i for i in range(size)]
        return data
    except MemoryError:
        # Fallback to disk-based processing
        return await disk_based_processing(size)
```

## Error Handling Best Practices

1. **Fail Fast**: Validate inputs early in the workflow
2. **Specific Exceptions**: Use specific exception types for different error conditions
3. **Cleanup**: Always perform necessary cleanup in exception handlers
4. **Logging**: Log errors with sufficient context for debugging
5. **Monitoring**: Set up alerts for critical error patterns
6. **Testing**: Test error scenarios as thoroughly as success scenarios
7. **Documentation**: Document expected error conditions and recovery strategies

## Running Error Handling Examples

```bash
# Test timeout handling
python -c "
import asyncio
from examples.error_handling import timeout_handling_workflow
ctx = timeout_handling_workflow.run('test data')
print(ctx.to_json())
"

# Test retry mechanisms
python -c "
from examples.error_handling import retry_workflow
ctx = retry_workflow.run('test data')
print(ctx.to_json())
"

# Test cancellation
python examples/cancellation.py
```

## Debugging Failed Workflows

When workflows fail, use these debugging techniques:

1. **Execution Context**: Examine `ctx.events` for detailed execution history
2. **Error Details**: Check `ctx.error` for exception information
3. **Partial Results**: Use `ctx.partial_results` to see intermediate outputs
4. **Logging**: Enable debug logging for detailed execution traces
5. **Testing**: Create unit tests for individual tasks that failed
