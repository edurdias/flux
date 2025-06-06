# Adding Error Handling

Error handling is a critical aspect of building robust workflows. Flux provides multiple mechanisms to handle failures gracefully, including retries, fallback handlers, and rollback procedures. This tutorial will show you how to make your workflows resilient to various types of failures.

## What You'll Learn

By the end of this tutorial, you'll understand:
- How to configure retry policies for tasks
- How to implement fallback handlers
- How to use rollback procedures for cleanup
- Best practices for error handling in workflows

## Prerequisites

- Complete the [Simple Workflow](simple-workflow.md) tutorial
- Basic understanding of Python exception handling
- Flux installed and working

## Understanding Error Types

Before implementing error handling, it's important to understand the types of errors you might encounter:

### Transient Errors
These are temporary failures that might succeed if retried:
- Network timeouts
- Database connection issues
- Rate limiting from APIs
- Temporary resource unavailability

### Permanent Errors
These are failures that won't resolve with retries:
- Invalid input data
- Authentication failures
- Logic errors in your code
- Missing resources that won't appear

## Implementing Retry Logic

The most basic error handling mechanism is retrying failed tasks. Here's how to add retry logic to your tasks:

```python
from flux import ExecutionContext, task, workflow
import httpx
import asyncio

@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2
)
async def fetch_user_data(user_id: str):
    """Fetch user data with automatic retries."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.example.com/users/{user_id}")
        response.raise_for_status()
        return response.json()

@workflow
async def get_user_profile(ctx: ExecutionContext[str]):
    user_data = await fetch_user_data(ctx.input)
    return {
        "user_id": ctx.input,
        "name": user_data["name"],
        "email": user_data["email"]
    }
```

### Retry Configuration Options

- `retry_max_attempts`: Maximum number of retry attempts (default: 0, no retries)
- `retry_delay`: Initial delay between retries in seconds (default: 1)
- `retry_backoff`: Multiplier for exponential backoff (default: 2)

With the configuration above:
- First retry after 1 second
- Second retry after 2 seconds (1 × 2)
- Third retry after 4 seconds (2 × 2)

## Adding Fallback Handlers

When retries aren't enough, fallback handlers provide alternative logic:

```python
@task
async def get_user_from_cache(user_id: str):
    """Fallback to get user data from cache."""
    # Simulate cache lookup
    cache_data = {
        "123": {"name": "John Doe", "email": "john@example.com"},
        "456": {"name": "Jane Smith", "email": "jane@example.com"}
    }
    return cache_data.get(user_id, {"name": "Unknown User", "email": "unknown@example.com"})

@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    fallback=get_user_from_cache
)
async def fetch_user_data_with_fallback(user_id: str):
    """Fetch user data with fallback to cache."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.example.com/users/{user_id}")
        response.raise_for_status()
        return response.json()

@workflow
async def resilient_user_profile(ctx: ExecutionContext[str]):
    user_data = await fetch_user_data_with_fallback(ctx.input)
    return {
        "user_id": ctx.input,
        "name": user_data["name"],
        "email": user_data["email"],
        "source": "api" if "api" in str(user_data) else "cache"
    }
```

## Implementing Rollback Procedures

Rollback handlers help clean up resources when tasks fail:

```python
@task
async def cleanup_temp_file(file_path: str):
    """Clean up temporary files."""
    import os
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"Cleaned up temporary file: {file_path}")

@task.with_options(
    retry_max_attempts=2,
    rollback=cleanup_temp_file
)
async def process_file(file_path: str):
    """Process a file with automatic cleanup on failure."""
    # Simulate file processing that might fail
    if "invalid" in file_path:
        raise ValueError("Invalid file format")

    # Normal processing
    with open(file_path, 'r') as f:
        content = f.read()

    return {"lines": len(content.splitlines()), "chars": len(content)}

@workflow
async def file_processing_workflow(ctx: ExecutionContext[str]):
    result = await process_file(ctx.input)
    return result
```

## Handling Timeouts

Set timeouts to prevent tasks from running indefinitely:

```python
@task.with_options(
    timeout=30,  # 30 seconds timeout
    retry_max_attempts=2
)
async def slow_operation(data: str):
    """An operation that might take too long."""
    # Simulate a potentially slow operation
    await asyncio.sleep(5)  # This will succeed
    return f"Processed: {data}"

@task.with_options(
    timeout=10,
    fallback=lambda x: f"Timeout fallback for: {x}"
)
async def risky_operation(data: str):
    """An operation that might timeout."""
    await asyncio.sleep(15)  # This will timeout and trigger fallback
    return f"Completed: {data}"

@workflow
async def timeout_demo(ctx: ExecutionContext[str]):
    # This will succeed
    result1 = await slow_operation(ctx.input)

    # This will timeout and use fallback
    result2 = await risky_operation(ctx.input)

    return {
        "slow_result": result1,
        "risky_result": result2
    }
```

## Comprehensive Error Handling Example

Here's a complete example that combines all error handling mechanisms:

```python
from flux import ExecutionContext, task, workflow
import httpx
import asyncio
from typing import Dict, Any

@task
async def log_error(error_info: Dict[str, Any]):
    """Log error information for monitoring."""
    print(f"Error logged: {error_info}")
    return {"logged": True}

@task
async def get_default_weather():
    """Fallback weather data."""
    return {
        "temperature": 20,
        "condition": "Unknown",
        "source": "default"
    }

@task.with_options(
    retry_max_attempts=3,
    retry_delay=2,
    retry_backoff=1.5,
    timeout=10,
    fallback=get_default_weather,
    rollback=log_error
)
async def fetch_weather(city: str):
    """Fetch weather with comprehensive error handling."""
    async with httpx.AsyncClient() as client:
        # Simulate API call that might fail
        response = await client.get(
            f"https://api.weather.com/current/{city}",
            timeout=8.0
        )
        response.raise_for_status()

        data = response.json()
        return {
            "temperature": data["temp"],
            "condition": data["condition"],
            "source": "api"
        }

@task.with_options(retry_max_attempts=2)
async def format_weather_report(weather_data: Dict[str, Any]):
    """Format weather data into a report."""
    temp = weather_data["temperature"]
    condition = weather_data["condition"]
    source = weather_data.get("source", "unknown")

    return f"Weather Report: {temp}°C, {condition} (source: {source})"

@workflow
async def weather_report_workflow(ctx: ExecutionContext[str]):
    """Complete weather report workflow with error handling."""
    try:
        # Fetch weather data (with built-in error handling)
        weather_data = await fetch_weather(ctx.input)

        # Format the report
        report = await format_weather_report(weather_data)

        return {
            "city": ctx.input,
            "report": report,
            "success": True
        }

    except Exception as e:
        # Workflow-level error handling
        return {
            "city": ctx.input,
            "error": str(e),
            "success": False
        }

# Example usage
if __name__ == "__main__":
    # Test with a city
    result = weather_report_workflow.run("London")
    print(result.output)
```

## Best Practices

### 1. Choose Appropriate Retry Strategies
- Use retries for transient failures only
- Implement exponential backoff to avoid overwhelming services
- Set reasonable maximum retry attempts

### 2. Design Effective Fallbacks
- Ensure fallback functions have the same signature as the main task
- Provide meaningful default values
- Consider degraded functionality over complete failure

### 3. Implement Proper Cleanup
- Use rollback handlers to clean up resources
- Ensure rollback operations are idempotent
- Log rollback actions for debugging

### 4. Set Reasonable Timeouts
- Consider the expected execution time of your tasks
- Account for network latency and processing time
- Use timeouts to prevent resource exhaustion

### 5. Monitor and Log Errors
- Log all error occurrences for monitoring
- Include context information in error logs
- Set up alerting for critical failures

## Testing Error Handling

Always test your error handling logic:

```python
import pytest
from unittest.mock import patch

@pytest.mark.asyncio
async def test_retry_behavior():
    """Test that retries work as expected."""
    call_count = 0

    @task.with_options(retry_max_attempts=2)
    async def failing_task():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("Temporary failure")
        return "Success"

    result = await failing_task()
    assert result == "Success"
    assert call_count == 3  # Initial call + 2 retries

@pytest.mark.asyncio
async def test_fallback_behavior():
    """Test that fallback is called when task fails."""
    @task
    async def fallback_handler():
        return "Fallback result"

    @task.with_options(fallback=fallback_handler)
    async def always_failing_task():
        raise Exception("Always fails")

    result = await always_failing_task()
    assert result == "Fallback result"
```

## Next Steps

Now that you understand error handling in Flux:

1. Try the [Parallel Execution](parallel-execution.md) tutorial to learn about concurrent workflows
2. Explore [Pipeline Processing](pipeline-processing.md) for sequential task chains
3. Review the [User Guide](../../user-guide/error-management.md) for advanced error handling patterns

## Summary

In this tutorial, you learned how to make your Flux workflows resilient through:

- **Retry Logic**: Automatic retries with exponential backoff
- **Fallback Handlers**: Alternative logic when tasks fail
- **Rollback Procedures**: Resource cleanup on failures
- **Timeout Management**: Preventing indefinite execution
- **Best Practices**: Guidelines for robust error handling

These error handling mechanisms ensure your workflows can gracefully handle failures and continue operating even when external dependencies are unreliable.
