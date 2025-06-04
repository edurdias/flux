# Task Definition and Decoration

Tasks are the fundamental building blocks of Flux workflows. This guide covers everything you need to know about defining, configuring, and using tasks effectively in your workflows.

## What You'll Learn

This guide covers:
- Basic task creation and decoration
- Task configuration options and parameters
- Input/output handling and type safety
- Advanced task features and patterns
- Best practices for task design

## Basic Task Creation

### The @task Decorator

The `@task` decorator transforms regular Python functions into Flux tasks:

```python
from flux import task

@task
async def simple_task(name: str):
    """A simple task that returns a greeting."""
    return f"Hello, {name}!"

# Usage in workflow
@workflow
async def greeting_workflow(ctx: ExecutionContext[str]):
    greeting = await simple_task(ctx.input)
    return greeting
```

### Synchronous vs Asynchronous Tasks

Flux supports both synchronous and asynchronous task functions:

```python
# Asynchronous task (recommended)
@task
async def async_task(data: str):
    await asyncio.sleep(1)  # Can use await
    return data.upper()

# Synchronous task (will be wrapped in async)
@task
async def sync_task(data: str):
    # For CPU-bound operations, use asyncio.to_thread or similar
    import asyncio
    result = await asyncio.to_thread(time.sleep, 1)  # Non-blocking equivalent
    return data.lower()
```

**Recommendation**: Use async tasks for better performance and resource utilization.

## Task Configuration Options

### Using with_options for Advanced Configuration

Use `@task.with_options()` to configure task behavior:

```python
@task.with_options(
    name="custom_task_name",
    retry_max_attempts=3,
    retry_delay=2,
    retry_backoff=1.5,
    timeout=30,
    cache=True,
    metadata=True
)
async def configured_task(data: str):
    """A task with custom configuration."""
    return data.upper()
```

### Configuration Parameters

#### Naming
```python
@task.with_options(name="process_user_{user_id}")
async def process_user(user_id: str, data: dict):
    """Task name can include dynamic values from parameters."""
    return f"Processed user {user_id}"
```

#### Retry Configuration
```python
@task.with_options(
    retry_max_attempts=5,    # Number of retry attempts
    retry_delay=1,           # Initial delay in seconds
    retry_backoff=2          # Backoff multiplier (exponential)
)
async def unreliable_api_call(endpoint: str):
    """Task that retries on failure with exponential backoff."""
    async with httpx.AsyncClient() as client:
        response = await client.get(endpoint)
        response.raise_for_status()
        return response.json()
```

#### Timeout Configuration
```python
@task.with_options(timeout=10)  # 10 seconds timeout
async def time_limited_task(data: str):
    """Task that must complete within 10 seconds."""
    # Long-running operation
    await asyncio.sleep(5)
    return f"Processed: {data}"
```

#### Caching
```python
@task.with_options(cache=True)
async def expensive_computation(input_value: int):
    """Results are cached based on input parameters."""
    # Expensive computation
    await asyncio.sleep(2)
    return input_value ** 2
```

## Error Handling and Recovery

### Fallback Handlers

Provide alternative logic when tasks fail:

```python
@task
async def fallback_handler(original_input: str):
    """Fallback logic when main task fails."""
    return f"Fallback result for: {original_input}"

@task.with_options(
    retry_max_attempts=2,
    fallback=fallback_handler
)
async def risky_task(data: str):
    """Task with fallback handling."""
    if random.random() < 0.7:  # 70% chance of failure
        raise Exception("Task failed")
    return f"Success: {data}"
```

### Rollback Procedures

Clean up resources when tasks fail:

```python
@task
async def cleanup_resources(resource_id: str):
    """Cleanup function for failed tasks."""
    print(f"Cleaning up resource: {resource_id}")
    # Cleanup logic here

@task.with_options(
    rollback=cleanup_resources,
    retry_max_attempts=1
)
async def create_resource(resource_id: str):
    """Create a resource with automatic cleanup on failure."""
    # Resource creation logic
    if random.random() < 0.5:
        raise Exception("Resource creation failed")
    return f"Created resource: {resource_id}"
```

### Combining Error Handling Mechanisms

```python
@task
async def log_failure(error_context: str):
    """Log task failures for monitoring."""
    print(f"Task failed: {error_context}")

@task
async def default_response(input_data: str):
    """Provide default response on failure."""
    return {"status": "error", "data": input_data, "message": "Using default"}

@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=15,
    fallback=default_response,
    rollback=log_failure
)
async def robust_task(data: str):
    """A task with comprehensive error handling."""
    # Simulate potentially failing operation
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.example.com/process/{data}")
        response.raise_for_status()
        return response.json()
```

## Input/Output Handling

### Type Safety and Hints

Use Python type hints for better code clarity and IDE support:

```python
from typing import Dict, List, Optional, Union
from pydantic import BaseModel

# Using built-in types
@task
async def process_numbers(numbers: List[int]) -> Dict[str, float]:
    """Process a list of numbers and return statistics."""
    return {
        "count": len(numbers),
        "sum": sum(numbers),
        "average": sum(numbers) / len(numbers) if numbers else 0.0
    }

# Using Pydantic models for complex data
class UserProfile(BaseModel):
    user_id: str
    name: str
    email: str
    age: int

class ProcessedProfile(BaseModel):
    user_id: str
    display_name: str
    email_domain: str
    age_category: str

@task
async def process_user_profile(profile: UserProfile) -> ProcessedProfile:
    """Process user profile with type safety."""
    age_category = "adult" if profile.age >= 18 else "minor"

    return ProcessedProfile(
        user_id=profile.user_id,
        display_name=profile.name.title(),
        email_domain=profile.email.split("@")[1],
        age_category=age_category
    )
```

### Optional and Default Parameters

```python
@task
async def format_message(
    message: str,
    prefix: str = "INFO",
    timestamp: Optional[str] = None,
    uppercase: bool = False
) -> str:
    """Format a message with optional parameters."""
    if timestamp is None:
        timestamp = datetime.now().isoformat()

    formatted = f"[{timestamp}] {prefix}: {message}"

    if uppercase:
        formatted = formatted.upper()

    return formatted

# Usage with different parameter combinations
@workflow
async def message_formatting_workflow(ctx: ExecutionContext[str]):
    message = ctx.input

    # Using defaults
    basic = await format_message(message)

    # With custom prefix
    warning = await format_message(message, prefix="WARNING")

    # With all parameters
    custom = await format_message(
        message,
        prefix="CUSTOM",
        timestamp="2025-06-04T10:00:00Z",
        uppercase=True
    )

    return {
        "basic": basic,
        "warning": warning,
        "custom": custom
    }
```

### Variable Arguments

```python
@task
async def process_multiple_items(*items: str) -> List[str]:
    """Process variable number of string items."""
    return [item.upper() for item in items]

@task
async def process_with_options(data: str, **options: Any) -> Dict[str, Any]:
    """Process data with flexible options."""
    result = {"original": data}

    if options.get("uppercase", False):
        result["processed"] = data.upper()

    if options.get("prefix"):
        result["processed"] = f"{options['prefix']}: {result.get('processed', data)}"

    return result
```

## Secrets Management

Tasks can request access to secrets managed by Flux:

```python
@task.with_options(secret_requests=["api_key", "database_url"])
async def secure_api_call(endpoint: str, ctx: ExecutionContext):
    """Task that uses secrets from the secret manager."""
    # Access secrets through the execution context
    api_key = await ctx.get_secret("api_key")

    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient() as client:
        response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        return response.json()

@task.with_options(secret_requests=["database_url"])
async def database_operation(query: str, ctx: ExecutionContext):
    """Task that performs database operations with secure connection."""
    db_url = await ctx.get_secret("database_url")

    # Use the database URL for connection
    # Database operation logic here
    return {"query": query, "status": "completed"}
```

## Output Storage

Configure custom output storage for task results:

```python
from flux.output_storage import FileOutputStorage

# Create custom output storage
file_storage = FileOutputStorage(base_path="./task_outputs")

@task.with_options(output_storage=file_storage)
async def generate_report(data: dict):
    """Task that stores output to file system."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "data_summary": {
            "total_records": len(data.get("records", [])),
            "processing_time": "1.5s"
        },
        "detailed_data": data
    }

    return report
```

## Task Metadata

Access task metadata during execution:

```python
@task.with_options(metadata=True)
async def metadata_aware_task(data: str, metadata: TaskMetadata):
    """Task that can access its own metadata."""
    return {
        "task_id": metadata.task_id,
        "task_name": metadata.task_name,
        "processed_data": data.upper(),
        "processing_timestamp": datetime.now().isoformat()
    }
```

## Advanced Task Patterns

### Conditional Task Execution

```python
@task
async def conditional_processor(data: dict) -> dict:
    """Process data conditionally based on its content."""
    if data.get("type") == "user":
        return await process_user_data(data)
    elif data.get("type") == "order":
        return await process_order_data(data)
    else:
        return {"error": "Unknown data type", "original": data}

@task
async def process_user_data(user_data: dict) -> dict:
    """Process user-specific data."""
    return {
        "type": "user",
        "processed": True,
        "name": user_data.get("name", "").title()
    }

@task
async def process_order_data(order_data: dict) -> dict:
    """Process order-specific data."""
    return {
        "type": "order",
        "processed": True,
        "total": order_data.get("amount", 0) * 1.1  # Add tax
    }
```

### Task Composition

```python
@task
async def validate_input(data: Any) -> Any:
    """Validate input data."""
    if not data:
        raise ValueError("Input data is required")
    return data

@task
async def transform_data(data: Any) -> Any:
    """Transform the data."""
    if isinstance(data, str):
        return data.upper()
    elif isinstance(data, (int, float)):
        return data * 2
    return data

@task
async def format_output(data: Any) -> dict:
    """Format the final output."""
    return {
        "result": data,
        "timestamp": datetime.now().isoformat(),
        "status": "completed"
    }

@task
async def composed_task(input_data: Any) -> dict:
    """A task that composes other tasks."""
    # Chain multiple task calls
    validated = await validate_input(input_data)
    transformed = await transform_data(validated)
    formatted = await format_output(transformed)

    return formatted
```

### Dynamic Task Creation

```python
def create_processor_task(processor_name: str, processing_func):
    """Factory function to create processor tasks."""

    @task.with_options(name=f"process_with_{processor_name}")
    async def processor_task(data: Any):
        return processing_func(data)

    return processor_task

# Create specific processor tasks
uppercase_task = create_processor_task("uppercase", lambda x: x.upper() if isinstance(x, str) else x)
double_task = create_processor_task("double", lambda x: x * 2 if isinstance(x, (int, float)) else x)
```

## Best Practices

### 1. Keep Tasks Focused and Single-Purpose

```python
# Good: Single responsibility
@task
async def validate_email(email: str) -> str:
    """Validate email format."""
    if "@" not in email:
        raise ValueError("Invalid email format")
    return email.lower()

@task
async def send_email(email: str, message: str) -> bool:
    """Send email to recipient."""
    # Email sending logic
    return True

# Avoid: Multiple responsibilities
@task
async def validate_and_send_email(email: str, message: str):
    """Validate email and send message (too many responsibilities)."""
    if "@" not in email:
        raise ValueError("Invalid email format")
    # Email sending logic
    return True
```

### 2. Use Descriptive Names and Documentation

```python
@task.with_options(name="fetch_user_profile_{user_id}")
async def fetch_user_profile(user_id: str) -> Dict[str, Any]:
    """
    Fetch user profile data from the external API.

    Args:
        user_id: Unique identifier for the user

    Returns:
        Dictionary containing user profile information

    Raises:
        HTTPError: If the API request fails
        ValueError: If user_id is invalid
    """
    if not user_id or not user_id.isalnum():
        raise ValueError(f"Invalid user_id: {user_id}")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.example.com/users/{user_id}")
        response.raise_for_status()
        return response.json()
```

### 3. Handle Errors Appropriately

```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    fallback=lambda x: {"error": "Service unavailable", "input": x}
)
async def external_service_call(data: str):
    """Call external service with proper error handling."""
    try:
        # External service call
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.example.com/process", json={"data": data})
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise  # Let retry mechanism handle timeouts
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            raise  # Retry on server errors
        else:
            # Don't retry on client errors
            raise ValueError(f"Client error: {e.response.status_code}")
```

### 4. Use Type Hints Consistently

```python
from typing import List, Dict, Optional, Union
from datetime import datetime

@task
async def process_user_list(
    users: List[Dict[str, Union[str, int]]],
    filter_active: bool = True,
    min_age: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Process a list of user dictionaries with type safety.

    Args:
        users: List of user dictionaries
        filter_active: Whether to filter for active users only
        min_age: Minimum age filter (optional)

    Returns:
        List of processed user dictionaries
    """
    processed_users = []

    for user in users:
        if filter_active and not user.get("active", True):
            continue

        if min_age is not None and user.get("age", 0) < min_age:
            continue

        processed_user = {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "processed_at": datetime.now().isoformat()
        }
        processed_users.append(processed_user)

    return processed_users
```

## Testing Tasks

Test tasks independently before using them in workflows:

```python
import pytest
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_fetch_user_profile_success():
    """Test successful user profile fetch."""
    with patch('httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": "123", "name": "John Doe"}
        mock_response.raise_for_status.return_value = None

        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        result = await fetch_user_profile("123")
        assert result["id"] == "123"
        assert result["name"] == "John Doe"

@pytest.mark.asyncio
async def test_fetch_user_profile_invalid_id():
    """Test error handling for invalid user ID."""
    with pytest.raises(ValueError, match="Invalid user_id"):
        await fetch_user_profile("invalid@id")

@pytest.mark.asyncio
async def test_composed_task():
    """Test task composition."""
    result = await composed_task("hello")
    assert result["result"] == "HELLO"
    assert "timestamp" in result
    assert result["status"] == "completed"
```

## Summary

This guide covered the essential aspects of task definition and decoration in Flux:

- **Basic Task Creation**: Using the `@task` decorator for simple and advanced tasks
- **Configuration Options**: Retry policies, timeouts, caching, and error handling
- **Input/Output Handling**: Type safety, optional parameters, and complex data structures
- **Advanced Features**: Secrets management, output storage, and metadata access
- **Best Practices**: Design patterns for maintainable and robust tasks

Tasks are the building blocks of your workflows. Well-designed tasks with proper configuration make your workflows more reliable, maintainable, and efficient.
