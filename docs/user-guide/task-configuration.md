# Task Configuration

Task configuration is at the heart of building robust, resilient workflows in Flux. This guide covers all aspects of task configuration, from basic retry policies to advanced patterns for handling complex failure scenarios.

## What You'll Learn

This guide covers:
- Retry policies and backoff strategies
- Timeout management and prevention
- Fallback and rollback handlers
- Caching strategies for performance
- Advanced configuration patterns
- Best practices for production deployments

## Basic Task Configuration

### Using @task.with_options()

The `@task.with_options()` decorator provides comprehensive configuration for task behavior:

```python
from flux import task

@task.with_options(
    retry_count=3,
    retry_delay=2,
    timeout=30,
    cache=True
)
async def configured_task(data: str) -> str:
    """Task with comprehensive configuration."""
    await sleep(1)  # Simulate processing
    return f"Processed: {data}"

# Usage in workflow
@workflow
async def configured_workflow(ctx: ExecutionContext[str]) -> str:
    result = await configured_task(ctx.input)
    return result
```

### Available Configuration Options

```python
@task.with_options(
    # Retry configuration
    retry_count=3,                    # Number of retry attempts
    retry_delay=1,                    # Base delay between retries (seconds)
    retry_backoff="exponential",      # Backoff strategy: "linear", "exponential", "fixed"
    retry_max_delay=60,              # Maximum delay between retries

    # Timeout configuration
    timeout=30,                       # Task execution timeout (seconds)

    # Caching configuration
    cache=True,                       # Enable result caching
    cache_ttl=3600,                  # Cache time-to-live (seconds)

    # Error handling
    fallback=fallback_function,       # Function to call on failure
    rollback=rollback_function,       # Function to call for cleanup

    # Output storage
    output_storage=storage_instance,  # External storage for large results

    # Resource requirements
    requests=resource_requirements    # CPU, memory, GPU requirements
)
async def fully_configured_task(data: any) -> any:
    """Task with all available configuration options."""
    return await process_data(data)
```

## Retry Policies and Backoff

### Retry Count and Delay

Configure how many times a task should retry and the delay between attempts:

```python
@task.with_options(
    retry_count=5,      # Retry up to 5 times
    retry_delay=2       # Wait 2 seconds between retries
)
async def basic_retry_task(data: str) -> str:
    """Task with basic retry configuration."""

    # Simulate potential failure
    import random
    if random.random() < 0.3:  # 30% failure rate
        raise ConnectionError("Network temporarily unavailable")

    return f"Successfully processed: {data}"
```

### Backoff Strategies

Choose different backoff strategies for retry delays:

```python
# Linear backoff: delay increases linearly (2s, 4s, 6s, 8s...)
@task.with_options(
    retry_count=4,
    retry_delay=2,
    retry_backoff="linear"
)
async def linear_backoff_task(data: str) -> str:
    """Task with linear backoff strategy."""
    return await external_api_call(data)

# Exponential backoff: delay doubles each time (1s, 2s, 4s, 8s...)
@task.with_options(
    retry_count=4,
    retry_delay=1,
    retry_backoff="exponential",
    retry_max_delay=30  # Cap at 30 seconds
)
async def exponential_backoff_task(data: str) -> str:
    """Task with exponential backoff strategy."""
    return await database_operation(data)

# Fixed backoff: same delay every time (3s, 3s, 3s, 3s...)
@task.with_options(
    retry_count=3,
    retry_delay=3,
    retry_backoff="fixed"
)
async def fixed_backoff_task(data: str) -> str:
    """Task with fixed backoff strategy."""
    return await file_operation(data)
```

### Conditional Retry Logic

Implement custom retry logic based on exception types:

```python
def should_retry(exception: Exception, attempt: int) -> bool:
    """Custom retry logic based on exception type and attempt number."""

    # Don't retry validation errors
    if isinstance(exception, ValueError):
        return False

    # Retry network errors up to 5 times
    if isinstance(exception, ConnectionError):
        return attempt < 5

    # Retry other exceptions up to 3 times
    return attempt < 3

@task.with_options(
    retry_count=5,
    retry_delay=2,
    retry_backoff="exponential"
)
async def conditional_retry_task(data: dict) -> dict:
    """Task with custom retry logic based on exception types."""

    try:
        # Validate input first (don't retry validation errors)
        if not data.get("required_field"):
            raise ValueError("Missing required field")

        # Attempt external operation (retry on network errors)
        result = await external_service_call(data)
        return result

    except Exception as e:
        # Log the exception for debugging
        print(f"Task failed with {type(e).__name__}: {e}")
        raise
```

## Timeout Management

### Basic Timeout Configuration

Set execution time limits to prevent hanging tasks:

```python
@task.with_options(timeout=30)  # 30-second timeout
async def timed_task(data: str) -> str:
    """Task with timeout protection."""

    # This operation must complete within 30 seconds
    result = await long_running_operation(data)
    return result

@task.with_options(timeout=5)   # Short timeout for quick operations
async def quick_task(data: str) -> str:
    """Task that should complete quickly."""

    # Simple operation that should be fast
    return data.upper()
```

### Timeout Handling Patterns

Handle timeouts gracefully in your workflows:

```python
@task.with_options(
    timeout=60,
    fallback=timeout_fallback_handler
)
async def timeout_sensitive_task(data: dict) -> dict:
    """Task with timeout and fallback handling."""

    try:
        # Attempt the primary operation
        result = await complex_computation(data)
        return {"success": True, "result": result}

    except asyncio.TimeoutError:
        # This will trigger the fallback handler
        raise

async def timeout_fallback_handler(task_args: dict) -> dict:
    """Fallback handler for timeout scenarios."""

    return {
        "success": False,
        "error": "timeout",
        "fallback_result": "partial_processing_completed"
    }

@workflow
async def timeout_aware_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that handles timeouts gracefully."""

    result = await timeout_sensitive_task(ctx.input)

    if not result["success"]:
        # Handle timeout scenario
        return {
            "status": "completed_with_fallback",
            "result": result,
            "execution_id": ctx.execution_id
        }

    return {
        "status": "completed_successfully",
        "result": result,
        "execution_id": ctx.execution_id
    }
```

## Fallback and Rollback Handlers

### Fallback Handlers

Provide alternative processing when tasks fail:

```python
async def api_fallback_handler(task_args: dict) -> dict:
    """Fallback handler for API failures."""

    # Extract original arguments
    original_data = task_args.get("data", {})

    # Provide alternative processing
    return {
        "source": "fallback",
        "data": original_data,
        "processed_at": await now(),
        "method": "local_cache"
    }

@task.with_options(
    retry_count=3,
    retry_delay=2,
    fallback=api_fallback_handler
)
async def api_task_with_fallback(data: dict) -> dict:
    """Task that falls back to local processing on API failure."""

    try:
        # Attempt API call
        result = await external_api_call(data)
        return {"source": "api", "data": result}

    except Exception as e:
        print(f"API call failed: {e}")
        # This will trigger the fallback handler
        raise
```

### Rollback Handlers

Clean up resources and state when tasks fail:

```python
async def database_rollback_handler(task_args: dict) -> None:
    """Rollback handler for database operations."""

    transaction_id = task_args.get("transaction_id")
    if transaction_id:
        # Rollback database transaction
        await rollback_transaction(transaction_id)
        print(f"Rolled back transaction {transaction_id}")

async def file_cleanup_rollback(task_args: dict) -> None:
    """Rollback handler for file operations."""

    temp_files = task_args.get("temp_files", [])
    for file_path in temp_files:
        try:
            # Clean up temporary files
            await cleanup_temp_file(file_path)
            print(f"Cleaned up temporary file: {file_path}")
        except Exception as e:
            print(f"Failed to clean up {file_path}: {e}")

@task.with_options(
    retry_count=2,
    rollback=database_rollback_handler
)
async def database_task_with_rollback(data: dict) -> dict:
    """Task with rollback capability for database operations."""

    transaction_id = await begin_transaction()

    try:
        # Store transaction ID for potential rollback
        # This is accessible in the rollback handler

        # Perform database operations
        result = await database_operations(data, transaction_id)
        await commit_transaction(transaction_id)

        return {"success": True, "result": result, "transaction_id": transaction_id}

    except Exception as e:
        # Rollback handler will be called automatically
        raise
```

### Combined Error Handling

Use both fallback and rollback handlers together:

```python
async def comprehensive_fallback(task_args: dict) -> dict:
    """Comprehensive fallback that provides alternative result."""

    return {
        "success": False,
        "fallback_used": True,
        "alternative_result": "cached_data",
        "timestamp": await now()
    }

async def comprehensive_rollback(task_args: dict) -> None:
    """Comprehensive rollback that cleans up all resources."""

    # Clean up database state
    if "transaction_id" in task_args:
        await rollback_transaction(task_args["transaction_id"])

    # Clean up file system
    if "temp_files" in task_args:
        for file_path in task_args["temp_files"]:
            await cleanup_file(file_path)

    # Clean up external resources
    if "external_resource_id" in task_args:
        await release_external_resource(task_args["external_resource_id"])

@task.with_options(
    retry_count=3,
    retry_delay=2,
    retry_backoff="exponential",
    fallback=comprehensive_fallback,
    rollback=comprehensive_rollback,
    timeout=60
)
async def comprehensive_task(data: dict) -> dict:
    """Task with comprehensive error handling configuration."""

    # Track resources for potential cleanup
    transaction_id = await begin_transaction()
    temp_files = []
    external_resource_id = None

    try:
        # Complex operation with multiple resources
        temp_file = await create_temp_file(data)
        temp_files.append(temp_file)

        external_resource_id = await acquire_external_resource()

        result = await complex_multi_resource_operation(
            data, transaction_id, temp_file, external_resource_id
        )

        await commit_transaction(transaction_id)
        return {"success": True, "result": result}

    except Exception as e:
        # Rollback handler will clean up all resources
        print(f"Task failed, rolling back: {e}")
        raise
```

## Caching Strategies

### Basic Result Caching

Cache task results to improve performance:

```python
@task.with_options(
    cache=True,
    cache_ttl=3600  # Cache for 1 hour
)
async def expensive_computation_task(input_data: dict) -> dict:
    """Task with result caching for expensive computations."""

    # Simulate expensive computation
    await sleep(5)

    result = await perform_complex_calculation(input_data)
    return result

@workflow
async def cached_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that benefits from task result caching."""

    # First call - computes and caches result
    result1 = await expensive_computation_task(ctx.input)

    # Second call with same input - returns cached result
    result2 = await expensive_computation_task(ctx.input)

    return {
        "first_call": result1,
        "second_call": result2,
        "cache_hit": result1 == result2
    }
```

### Cache Key Customization

Control how cache keys are generated:

```python
def custom_cache_key(func_name: str, args: tuple, kwargs: dict) -> str:
    """Generate custom cache key based on specific arguments."""

    # Only cache based on specific parameters
    relevant_data = kwargs.get("data", {})
    cache_key = f"{func_name}_{relevant_data.get('id')}_{relevant_data.get('version')}"
    return cache_key

@task.with_options(
    cache=True,
    cache_ttl=7200,  # Cache for 2 hours
    cache_key_func=custom_cache_key
)
async def selective_cache_task(data: dict, metadata: dict) -> dict:
    """Task with custom cache key generation."""

    # Only caches based on data.id and data.version
    # metadata changes don't affect cache
    result = await process_with_version_awareness(data)
    return result
```

### Conditional Caching

Implement caching logic based on result characteristics:

```python
def should_cache_result(result: any) -> bool:
    """Determine whether to cache based on result characteristics."""

    # Don't cache error results
    if isinstance(result, dict) and result.get("error"):
        return False

    # Don't cache small results (not worth the overhead)
    if len(str(result)) < 100:
        return False

    # Cache large, successful results
    return True

@task.with_options(
    cache=True,
    cache_ttl=1800,
    cache_condition=should_cache_result
)
async def conditional_cache_task(data: dict) -> dict:
    """Task with conditional caching based on result characteristics."""

    try:
        result = await potentially_large_computation(data)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

## Advanced Configuration Patterns

### Environment-Specific Configuration

Configure tasks differently based on environment:

```python
from flux.config import Configuration

config = Configuration.get().settings

# Environment-aware configuration
def get_task_config():
    if config.environment == "production":
        return {
            "retry_count": 5,
            "retry_delay": 3,
            "timeout": 120,
            "cache": True,
            "cache_ttl": 3600
        }
    elif config.environment == "staging":
        return {
            "retry_count": 3,
            "retry_delay": 2,
            "timeout": 60,
            "cache": True,
            "cache_ttl": 1800
        }
    else:  # development
        return {
            "retry_count": 1,
            "retry_delay": 1,
            "timeout": 30,
            "cache": False
        }

# Apply environment-specific configuration
task_config = get_task_config()

@task.with_options(**task_config)
async def environment_aware_task(data: dict) -> dict:
    """Task configured based on deployment environment."""
    return await process_data(data)
```

### Dynamic Configuration

Configure tasks dynamically based on input or runtime conditions:

```python
def get_dynamic_config(data: dict) -> dict:
    """Generate task configuration based on input data characteristics."""

    data_size = len(str(data))

    if data_size > 10000:  # Large data
        return {
            "timeout": 300,
            "retry_count": 5,
            "cache": True,
            "cache_ttl": 7200
        }
    elif data_size > 1000:  # Medium data
        return {
            "timeout": 60,
            "retry_count": 3,
            "cache": True,
            "cache_ttl": 3600
        }
    else:  # Small data
        return {
            "timeout": 30,
            "retry_count": 2,
            "cache": False
        }

@workflow
async def dynamic_config_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that configures tasks dynamically."""

    # Determine configuration based on input
    config = get_dynamic_config(ctx.input)

    # Apply configuration to task
    @task.with_options(**config)
    async def dynamically_configured_task(data: dict) -> dict:
        return await adaptive_processing(data)

    result = await dynamically_configured_task(ctx.input)
    return result
```

### Resource-Based Configuration

Configure tasks based on available resources:

```python
from flux.domain import ResourceRequest

def get_resource_aware_config(memory_gb: int, cpu_cores: int) -> dict:
    """Configure task based on available resources."""

    if memory_gb >= 16 and cpu_cores >= 8:
        # High-resource configuration
        return {
            "timeout": 600,
            "retry_count": 3,
            "cache": True,
            "requests": ResourceRequest(
                memory_mb=8192,
                cpu_cores=4
            )
        }
    elif memory_gb >= 8 and cpu_cores >= 4:
        # Medium-resource configuration
        return {
            "timeout": 300,
            "retry_count": 2,
            "cache": True,
            "requests": ResourceRequest(
                memory_mb=4096,
                cpu_cores=2
            )
        }
    else:
        # Low-resource configuration
        return {
            "timeout": 120,
            "retry_count": 1,
            "cache": False,
            "requests": ResourceRequest(
                memory_mb=2048,
                cpu_cores=1
            )
        }

# Apply resource-aware configuration
resource_config = get_resource_aware_config(memory_gb=16, cpu_cores=8)

@task.with_options(**resource_config)
async def resource_aware_task(data: dict) -> dict:
    """Task configured based on available system resources."""
    return await resource_intensive_processing(data)
```

## Best Practices

### 1. Start with Conservative Settings

Begin with conservative configuration and adjust based on observed behavior:

```python
@task.with_options(
    retry_count=2,      # Start conservatively
    retry_delay=1,      # Short initial delay
    timeout=60,         # Reasonable timeout
    cache=False         # Disable caching initially
)
async def new_task(data: dict) -> dict:
    """New task with conservative initial configuration."""
    return await new_operation(data)
```

### 2. Use Different Configs for Different Task Types

Tailor configuration to the specific characteristics of each task:

```python
# Quick, reliable tasks
@task.with_options(retry_count=1, timeout=10)
async def quick_task(data: str) -> str:
    return data.upper()

# Network-dependent tasks
@task.with_options(
    retry_count=5,
    retry_delay=2,
    retry_backoff="exponential",
    timeout=120
)
async def network_task(url: str) -> dict:
    return await fetch_from_api(url)

# CPU-intensive tasks
@task.with_options(
    retry_count=2,
    timeout=600,
    cache=True,
    cache_ttl=7200
)
async def cpu_intensive_task(data: list) -> list:
    return await complex_computation(data)
```

### 3. Monitor and Adjust Configuration

Regularly review task performance and adjust configuration:

```python
@task.with_options(
    retry_count=3,
    retry_delay=2,
    timeout=60,
    cache=True
)
async def monitored_task(data: dict) -> dict:
    """Task with monitoring for configuration optimization."""

    start_time = await now()

    try:
        result = await monitored_operation(data)

        # Log successful execution metrics
        execution_time = await now() - start_time
        print(f"Task completed in {execution_time}s")

        return result
    except Exception as e:
        # Log failure metrics for configuration tuning
        print(f"Task failed after {await now() - start_time}s: {e}")
        raise

# Monitor task performance and adjust configuration accordingly
```

### 4. Document Configuration Decisions

Document why specific configuration choices were made:

```python
@task.with_options(
    retry_count=5,      # High retry count due to known API instability
    retry_delay=3,      # Longer delay to respect API rate limits
    retry_backoff="exponential",  # Exponential backoff for better API behavior
    timeout=180,        # Extended timeout for large data transfers
    cache=True,         # Cache enabled due to expensive computation
    cache_ttl=3600      # 1-hour TTL balances freshness vs performance
)
async def well_documented_task(data: dict) -> dict:
    """
    Task configuration optimized for external API integration.

    Configuration rationale:
    - High retry count: External API has known intermittent issues
    - Exponential backoff: Reduces load on recovering API
    - Extended timeout: Large responses can take 2+ minutes
    - Caching enabled: Results are expensive and relatively stable
    """
    return await external_api_integration(data)
```

## Summary

This guide covered the comprehensive task configuration capabilities in Flux:

- **Basic Configuration**: Using `@task.with_options()` for retry, timeout, and caching
- **Retry Policies**: Different backoff strategies and conditional retry logic
- **Timeout Management**: Preventing hanging tasks and handling timeout scenarios
- **Error Handling**: Fallback and rollback handlers for robust failure management
- **Caching Strategies**: Performance optimization through intelligent result caching
- **Advanced Patterns**: Environment-specific and dynamic configuration approaches

Proper task configuration is essential for building production-ready workflows that handle failures gracefully, perform efficiently, and provide reliable results under various conditions.
