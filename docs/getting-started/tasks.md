# Tasks

Tasks are the fundamental building blocks of Flux workflows. They are Python functions decorated with `@task` that represent individual units of work within your application. Tasks can be simple operations or complex processes with sophisticated configuration options for error handling, retries, timeouts, and more.

## Task Definition

### Basic Task Creation

The simplest way to define a task is using the `@task` decorator:

```python
from flux import task

@task
async def process_data(data: str) -> str:
    """Process input data and return uppercase result."""
    return data.upper()

@task
async def calculate_sum(numbers: list[int]) -> int:
    """Calculate the sum of a list of numbers."""
    return sum(numbers)

@task
async def send_email(recipient: str, message: str) -> bool:
    """Send an email notification."""
    # Email sending logic here
    print(f"Email sent to {recipient}: {message}")
    return True
```

### Task Requirements

Tasks must follow these guidelines:

- **Function signature**: Can be sync or async functions
- **Parameters**: Support typed parameters for better IDE support
- **Return values**: Can return any Python object
- **Type hints**: Recommended for better development experience

```python
from typing import Dict, List, Optional

@task
async def fetch_user_data(user_id: int) -> Optional[Dict[str, str]]:
    """Fetch user data from database."""
    # Database query logic
    return {"id": str(user_id), "name": "John Doe"}

@task
async def validate_input(data: Dict[str, str]) -> List[str]:
    """Validate input data and return list of errors."""
    errors = []
    if not data.get("name"):
        errors.append("Name is required")
    if not data.get("email"):
        errors.append("Email is required")
    return errors
```

## Task Decoration

### Using @task.with_options

For advanced configuration, use `@task.with_options()` to specify task behavior:

```python
@task.with_options(
    name="custom_task_name",        # Custom task identifier
    retry_max_attempts=3,           # Maximum retry attempts
    retry_delay=1,                  # Initial delay between retries (seconds)
    retry_backoff=2,                # Exponential backoff multiplier
    timeout=30,                     # Task execution timeout (seconds)
    fallback=fallback_handler,      # Fallback function for failures
    rollback=rollback_handler,      # Rollback function for cleanup
    secret_requests=["API_KEY"],    # Required secrets
    cache=True,                     # Enable result caching
    metadata=True,                  # Enable task metadata access
    output_storage=custom_storage   # Custom output storage
)
async def advanced_task(data: str) -> str:
    """A task with comprehensive configuration."""
    return process_data(data)
```

### Decorator Parameters

#### name (str, optional)
Custom task name used for identification in logs and events. Supports string formatting with task parameters:

```python
@task.with_options(name="process_user_{user_id}")
async def process_user(user_id: int):
    return f"Processed user {user_id}"
```

#### retry_max_attempts (int, default: 0)
Maximum number of retry attempts when the task fails:

```python
@task.with_options(retry_max_attempts=3)
async def unreliable_api_call():
    # Will retry up to 3 times on failure
    response = await external_api_call()
    return response
```

#### retry_delay (int, default: 1)
Initial delay in seconds between retry attempts:

```python
@task.with_options(retry_max_attempts=3, retry_delay=2)
async def task_with_custom_delay():
    # First retry after 2 seconds, then 4, then 8 (with backoff=2)
    pass
```

#### retry_backoff (int, default: 2)
Exponential backoff multiplier for retry delays:

```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=3
)
async def task_with_backoff():
    # Retry delays: 1s, 3s, 9s
    pass
```

#### timeout (int, default: 0)
Task execution timeout in seconds. Zero means no timeout:

```python
@task.with_options(timeout=30)
async def time_sensitive_task():
    # Task will be cancelled if it runs longer than 30 seconds
    await long_running_operation()
```

## Task Configuration

### Error Handling Configuration

#### Fallback Functions

Fallback functions are called when a task fails after all retries are exhausted:

```python
async def email_fallback(recipient: str, message: str):
    """Fallback to logging when email fails."""
    print(f"Failed to send email to {recipient}. Message: {message}")
    return False

@task.with_options(
    retry_max_attempts=3,
    fallback=email_fallback
)
async def send_email(recipient: str, message: str):
    """Send email with fallback to logging."""
    # Email sending logic that might fail
    if not send_email_via_smtp(recipient, message):
        raise Exception("SMTP send failed")
    return True
```

#### Rollback Functions

Rollback functions handle cleanup when a task fails:

```python
async def cleanup_temp_files(file_path: str):
    """Clean up temporary files on failure."""
    import os
    if os.path.exists(file_path):
        os.remove(file_path)
    print(f"Cleaned up {file_path}")

@task.with_options(rollback=cleanup_temp_files)
async def process_file(file_path: str):
    """Process file with automatic cleanup on failure."""
    # Create temporary file
    temp_file = create_temp_file(file_path)

    # Process the file (might fail)
    result = complex_file_processing(temp_file)

    return result
```

### Performance Configuration

#### Result Caching

Enable caching to avoid re-executing expensive operations:

```python
@task.with_options(cache=True)
async def expensive_calculation(input_data: str) -> dict:
    """Expensive operation with result caching."""
    # Results are cached based on input parameters
    result = perform_complex_computation(input_data)
    return result

# First call executes the function
result1 = await expensive_calculation("data1")

# Second call with same input returns cached result
result2 = await expensive_calculation("data1")  # Uses cache
```

#### Task Metadata

Access task runtime information for debugging and monitoring:

```python
from flux.task import TaskMetadata

@task.with_options(metadata=True)
async def introspective_task(data: str, metadata: TaskMetadata):
    """Task that can access its own metadata."""
    print(f"Task ID: {metadata.task_id}")
    print(f"Task Name: {metadata.task_name}")

    # Use metadata for logging or conditional logic
    if "urgent" in metadata.task_name:
        return process_urgently(data)
    else:
        return process_normally(data)
```

### Security Configuration

#### Secret Requests

Request secure secrets for API keys, passwords, and sensitive data:

```python
@task.with_options(secret_requests=["DATABASE_URL", "API_KEY"])
async def secure_data_fetch(query: str, secrets: dict = None):
    """Fetch data using secure credentials."""
    db_url = secrets["DATABASE_URL"]
    api_key = secrets["API_KEY"]

    # Use secrets for secure operations
    connection = create_database_connection(db_url)
    result = fetch_data_with_auth(connection, query, api_key)

    return result
```

#### Custom Output Storage

Implement custom storage for task results:

```python
from flux.output_storage import OutputStorage

class S3OutputStorage(OutputStorage):
    def store(self, task_id: str, output: any) -> str:
        # Store output in S3 and return reference
        s3_key = f"task-outputs/{task_id}.json"
        upload_to_s3(s3_key, output)
        return s3_key

    def retrieve(self, reference: str) -> any:
        # Retrieve output from S3
        return download_from_s3(reference)

@task.with_options(output_storage=S3OutputStorage())
async def large_result_task():
    """Task with custom storage for large results."""
    # Generate large result
    large_data = generate_large_dataset()
    return large_data
```

## Advanced Task Features

### Task Mapping

Execute a task across multiple inputs in parallel:

```python
@task
async def process_item(item: str) -> str:
    """Process a single item."""
    return item.upper()

# In a workflow
async def batch_processing_workflow(ctx: ExecutionContext):
    items = ["item1", "item2", "item3", "item4"]

    # Process all items in parallel
    results = await process_item.map(items)

    return results  # ["ITEM1", "ITEM2", "ITEM3", "ITEM4"]
```

### Error Handling Patterns

#### Retry with Exponential Backoff

```python
@task.with_options(
    retry_max_attempts=5,
    retry_delay=1,
    retry_backoff=2
)
async def api_call_with_backoff():
    """API call with exponential backoff: 1s, 2s, 4s, 8s, 16s"""
    response = await external_api.get("/data")
    if response.status_code != 200:
        raise Exception(f"API call failed: {response.status_code}")
    return response.json()
```

#### Fallback Chain

```python
async def primary_service_fallback():
    """Fallback to secondary service."""
    return await secondary_service()

async def emergency_fallback():
    """Final fallback to cached data."""
    return get_cached_data()

@task.with_options(
    retry_max_attempts=2,
    fallback=primary_service_fallback
)
async def primary_service():
    """Primary service with fallback chain."""
    return await primary_api.get_data()

@task.with_options(fallback=emergency_fallback)
async def secondary_service():
    """Secondary service with emergency fallback."""
    return await secondary_api.get_data()
```

### Task Composition Patterns

#### Sequential Task Chain

```python
@task
async def fetch_data(source: str) -> dict:
    """Fetch raw data from source."""
    return {"raw_data": f"data from {source}"}

@task
async def transform_data(data: dict) -> dict:
    """Transform raw data."""
    return {"processed": data["raw_data"].upper()}

@task
async def save_data(data: dict) -> bool:
    """Save processed data."""
    print(f"Saved: {data}")
    return True

# Use in workflow
async def data_pipeline_workflow(ctx: ExecutionContext):
    # Sequential execution
    raw_data = await fetch_data("database")
    processed_data = await transform_data(raw_data)
    success = await save_data(processed_data)
    return success
```

#### Conditional Task Execution

```python
@task
async def validate_input(data: dict) -> bool:
    """Validate input data."""
    return "required_field" in data

@task
async def process_valid_data(data: dict) -> dict:
    """Process valid data."""
    return {"result": "processed", "input": data}

@task
async def handle_invalid_data(data: dict) -> dict:
    """Handle invalid data."""
    return {"error": "Invalid input", "input": data}

# Use in workflow with conditions
async def conditional_workflow(ctx: ExecutionContext):
    data = ctx.input

    is_valid = await validate_input(data)

    if is_valid:
        return await process_valid_data(data)
    else:
        return await handle_invalid_data(data)
```

## Best Practices

### Task Design Guidelines

1. **Single Responsibility**: Each task should have a single, well-defined purpose
2. **Idempotency**: Tasks should produce the same result when run multiple times
3. **Error Handling**: Always consider failure scenarios and implement appropriate handling
4. **Type Hints**: Use type hints for better development experience and documentation
5. **Documentation**: Include clear docstrings explaining the task's purpose and behavior

### Configuration Recommendations

1. **Timeouts**: Set reasonable timeouts for tasks that might hang
2. **Retries**: Use retries for transient failures, but avoid for permanent errors
3. **Caching**: Enable caching for expensive, deterministic operations
4. **Secrets**: Always use secret management for sensitive data
5. **Fallbacks**: Implement fallbacks for critical operations

### Performance Considerations

1. **Resource Usage**: Consider memory and CPU usage for large-scale operations
2. **I/O Operations**: Use async/await properly for I/O-bound tasks
3. **Batch Processing**: Use task mapping for processing multiple similar items
4. **Output Size**: Use custom storage for large task outputs

## Example: Complete Task Configuration

Here's a comprehensive example showing various task configuration options:

```python
from flux import task, ExecutionContext
from flux.task import TaskMetadata
from flux.output_storage import OutputStorage
import asyncio

# Custom storage implementation
class DatabaseStorage(OutputStorage):
    def store(self, task_id: str, output: any) -> str:
        # Store in database and return ID
        record_id = save_to_database(task_id, output)
        return record_id

    def retrieve(self, reference: str) -> any:
        return load_from_database(reference)

# Fallback function
async def notification_fallback(user_id: int, message: str):
    """Log notification failure."""
    print(f"Failed to notify user {user_id}: {message}")
    return {"status": "fallback", "logged": True}

# Rollback function
async def cleanup_resources(user_id: int, message: str):
    """Clean up any allocated resources."""
    print(f"Cleaning up resources for user {user_id}")

@task.with_options(
    name="notify_user_{user_id}",
    retry_max_attempts=3,
    retry_delay=2,
    retry_backoff=2,
    timeout=30,
    fallback=notification_fallback,
    rollback=cleanup_resources,
    secret_requests=["EMAIL_API_KEY", "SMS_API_KEY"],
    cache=True,
    metadata=True,
    output_storage=DatabaseStorage()
)
async def send_notification(
    user_id: int,
    message: str,
    secrets: dict = None,
    metadata: TaskMetadata = None
) -> dict:
    """
    Send notification to user with comprehensive error handling.

    Args:
        user_id: The ID of the user to notify
        message: The notification message
        secrets: Injected secrets (EMAIL_API_KEY, SMS_API_KEY)
        metadata: Task metadata for debugging

    Returns:
        Dictionary with notification status and details
    """
    print(f"Task {metadata.task_id} sending notification to user {user_id}")

    # Try email first
    try:
        email_result = await send_email_notification(
            user_id, message, secrets["EMAIL_API_KEY"]
        )
        return {"status": "success", "method": "email", "result": email_result}
    except Exception as e:
        print(f"Email failed: {e}")

    # Fallback to SMS
    try:
        sms_result = await send_sms_notification(
            user_id, message, secrets["SMS_API_KEY"]
        )
        return {"status": "success", "method": "sms", "result": sms_result}
    except Exception as e:
        print(f"SMS failed: {e}")
        raise Exception("All notification methods failed")

# Usage in workflow
async def user_workflow(ctx: ExecutionContext):
    user_id = ctx.input["user_id"]
    message = ctx.input["message"]

    result = await send_notification(user_id, message)
    return result
```

This example demonstrates a production-ready task with comprehensive error handling, security considerations, performance optimizations, and proper documentation.
