# Built-in Tasks

This page documents the built-in tasks provided by Flux for common workflow operations.

## Overview

Flux includes a comprehensive library of built-in tasks to handle common workflow patterns:

- **Data Operations**: File I/O, data transformation, validation
- **Network Operations**: HTTP requests, API calls, webhooks
- **System Operations**: Command execution, file system operations
- **Control Flow**: Delays, conditionals, loops
- **Integration**: Database operations, cloud services, notifications

## Data Operations

### file_read

Read content from a file.

```python
from flux.tasks import file_read

@workflow
async def process_file_workflow(context: ExecutionContext):
    # Read text file
    content = await file_read("data/input.txt")

    # Read JSON file
    json_data = await file_read("config/settings.json", format="json")

    # Read CSV file
    csv_data = await file_read("data/users.csv", format="csv")

    return {"content": content, "config": json_data, "users": csv_data}
```

**Parameters:**
- `file_path` (str): Path to the file
- `format` (str): File format ("text", "json", "csv", "yaml", "binary")
- `encoding` (str): Text encoding (default: "utf-8")
- `csv_delimiter` (str): CSV delimiter (default: ",")

### file_write

Write content to a file.

```python
from flux.tasks import file_write

@workflow
async def save_results_workflow(context: ExecutionContext[dict]):
    results = context.input
    # Write JSON data
    await file_write("output/results.json", results, format="json")

    # Write CSV data
    await file_write("output/data.csv", results["records"], format="csv")

    # Write text with custom encoding
    await file_write("output/report.txt", results["summary"], encoding="utf-8")

    return "Files saved successfully"
```

**Parameters:**
- `file_path` (str): Path to the file
- `content` (Any): Content to write
- `format` (str): File format ("text", "json", "csv", "yaml", "binary")
- `encoding` (str): Text encoding (default: "utf-8")
- `mode` (str): File mode ("w", "a", "x")

### data_transform

Transform data using common operations.

```python
from flux.tasks import data_transform

@workflow
async def transform_data_workflow(context: ExecutionContext[list]):
    raw_data = context.input
    # Filter data
    filtered = await data_transform(
        raw_data,
        operation="filter",
        condition=lambda x: x["status"] == "active"
    )

    # Map/transform data
    transformed = await data_transform(
        filtered,
        operation="map",
        transform=lambda x: {**x, "processed": True}
    )

    # Group data
    grouped = await data_transform(
        transformed,
        operation="group_by",
        key="category"
    )

    return grouped
```

**Parameters:**
- `data` (Any): Input data
- `operation` (str): Transform operation ("filter", "map", "group_by", "sort", "unique")
- `condition` (callable): Filter condition function
- `transform` (callable): Map transform function
- `key` (str/callable): Grouping or sorting key

### data_validate

Validate data against schemas or rules.

```python
from flux.tasks import data_validate

@workflow
async def validate_workflow(context: ExecutionContext[dict]):
    user_data = context.input
    # Schema validation
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 0}
        },
        "required": ["name", "email"]
    }

    validation_result = await data_validate(
        user_data,
        schema=schema,
        schema_type="json_schema"
    )

    return validation_result
```

**Parameters:**
- `data` (Any): Data to validate
- `schema` (dict): Validation schema
- `schema_type` (str): Schema type ("json_schema", "pydantic", "custom")
- `strict` (bool): Strict validation mode

## Network Operations

### http_request

Make HTTP requests to external APIs.

```python
from flux.tasks import http_request

@workflow
async def api_integration_workflow(context: ExecutionContext):
    # GET request
    users = await http_request(
        method="GET",
        url="https://api.example.com/users",
        headers={"Authorization": "Bearer token"}
    )

    # POST request with data
    new_user = await http_request(
        method="POST",
        url="https://api.example.com/users",
        json={"name": "John Doe", "email": "john@example.com"},
        headers={"Content-Type": "application/json"}
    )

    # Request with retry logic
    reliable_request = await http_request(
        method="GET",
        url="https://unreliable-api.com/data",
        retries=3,
        retry_delay=5,
        timeout=30
    )

    return {"users": users, "new_user": new_user}
```

**Parameters:**
- `method` (str): HTTP method ("GET", "POST", "PUT", "DELETE", etc.)
- `url` (str): Request URL
- `headers` (dict): Request headers
- `params` (dict): URL parameters
- `json` (dict): JSON request body
- `data` (Any): Request body data
- `timeout` (int): Request timeout in seconds
- `retries` (int): Number of retry attempts
- `retry_delay` (int): Delay between retries

### webhook_send

Send webhook notifications.

```python
from flux.tasks import webhook_send

@workflow
async def notification_workflow(context: ExecutionContext[dict]):
    event_data = context.input
    # Send webhook with payload
    response = await webhook_send(
        url="https://hooks.example.com/webhook",
        payload=event_data,
        headers={"X-API-Key": "secret-key"}
    )

    # Send webhook with custom format
    slack_notification = await webhook_send(
        url="https://hooks.slack.com/services/...",
        payload={
            "text": f"Workflow completed: {event_data['workflow_name']}",
            "channel": "#notifications"
        }
    )

    return response
```

**Parameters:**
- `url` (str): Webhook URL
- `payload` (dict): Webhook payload
- `headers` (dict): Request headers
- `method` (str): HTTP method (default: "POST")
- `timeout` (int): Request timeout

## System Operations

### shell_command

Execute shell commands.

```python
from flux.tasks import shell_command

@workflow
async def system_workflow(context: ExecutionContext):
    # Simple command
    result = await shell_command("ls -la /tmp")

    # Command with input
    grep_result = await shell_command(
        "grep -n 'error'",
        input="line 1: info\nline 2: error\nline 3: warning",
        shell=True
    )

    # Command with environment variables
    env_result = await shell_command(
        "echo $CUSTOM_VAR",
        env={"CUSTOM_VAR": "hello world"},
        shell=True
    )

    return {
        "listing": result.stdout,
        "grep": grep_result.stdout,
        "env": env_result.stdout
    }
```

**Parameters:**
- `command` (str/list): Command to execute
- `input` (str): Input to send to command
- `cwd` (str): Working directory
- `env` (dict): Environment variables
- `timeout` (int): Command timeout
- `shell` (bool): Use shell for execution
- `capture_output` (bool): Capture stdout/stderr

### file_operations

Perform file system operations.

```python
from flux.tasks import file_operations

@workflow
async def file_management_workflow(context: ExecutionContext):
    # Copy files
    await file_operations(
        operation="copy",
        source="/path/to/source.txt",
        destination="/path/to/dest.txt"
    )

    # Create directory
    await file_operations(
        operation="mkdir",
        path="/path/to/new/directory",
        parents=True
    )

    # List directory contents
    contents = file_operations(
        operation="listdir",
        path="/path/to/directory",
        recursive=True
    )

    # Delete files/directories
    file_operations(
        operation="remove",
        path="/path/to/old/file.txt"
    )

    return contents
```

**Parameters:**
- `operation` (str): Operation type ("copy", "move", "remove", "mkdir", "listdir")
- `source` (str): Source path
- `destination` (str): Destination path
- `path` (str): Target path
- `recursive` (bool): Recursive operation
- `parents` (bool): Create parent directories

## Control Flow

### delay

Add delays to workflow execution.

```python
from flux.tasks import delay

@workflow
async def timed_workflow(context: ExecutionContext):
    # Fixed delay
    await delay(seconds=10)

    # Delay until specific time
    await delay(until="2024-01-01T12:00:00Z")

    # Delay with jitter for distributed systems
    await delay(seconds=30, jitter=5)  # 25-35 seconds

    return "Workflow completed after delays"
```

**Parameters:**
- `seconds` (int): Delay duration in seconds
- `until` (str/datetime): Delay until specific time
- `jitter` (int): Random jitter in seconds

### conditional_branch

Execute tasks based on conditions.

```python
from flux.tasks import conditional_branch

@workflow
async def branching_workflow(context: ExecutionContext[dict]):
    workflow_input = context.input
    user_type = workflow_input["user_type"]
    data = workflow_input["data"]

    result = await conditional_branch(
        condition=user_type == "premium",
        if_true=lambda: process_premium_user(data),
        if_false=lambda: process_regular_user(data)
    )

    return result
```

**Parameters:**
- `condition` (bool/callable): Condition to evaluate
- `if_true` (callable): Function to execute if true
- `if_false` (callable): Function to execute if false

### for_each

Iterate over collections with parallel or sequential execution.

```python
from flux.tasks import for_each

@workflow
async def batch_processing_workflow(context: ExecutionContext[list]):
    items = context.input
    # Sequential processing
    results = await for_each(
        items=items,
        task=process_item,
        parallel=False
    )

    # Parallel processing with limit
    parallel_results = await for_each(
        items=items,
        task=process_item,
        parallel=True,
        max_workers=5
    )

    return parallel_results
```

**Parameters:**
- `items` (list): Items to iterate over
- `task` (callable): Task function to apply
- `parallel` (bool): Enable parallel execution
- `max_workers` (int): Maximum parallel workers

## Database Operations

### database_query

Execute database queries.

```python
from flux.tasks import database_query

@workflow
def database_workflow():
    # Select query
    users = database_query(
        connection_string="postgresql://user:pass@localhost/db",
        query="SELECT * FROM users WHERE active = %s",
        params=[True]
    )

    # Insert query
    insert_result = database_query(
        connection_string="postgresql://user:pass@localhost/db",
        query="INSERT INTO logs (message) VALUES (%s)",
        params=["Workflow executed"],
        fetch=False
    )

    return users
```

**Parameters:**
- `connection_string` (str): Database connection string
- `query` (str): SQL query
- `params` (list): Query parameters
- `fetch` (bool): Fetch results for SELECT queries
- `fetch_mode` (str): Fetch mode ("all", "one", "many")

### database_transaction

Execute multiple queries in a transaction.

```python
from flux.tasks import database_transaction

@workflow
def transactional_workflow(user_data: dict):
    queries = [
        {
            "query": "INSERT INTO users (name, email) VALUES (%s, %s)",
            "params": [user_data["name"], user_data["email"]]
        },
        {
            "query": "INSERT INTO user_preferences (user_id, theme) VALUES (LASTVAL(), %s)",
            "params": [user_data.get("theme", "light")]
        }
    ]

    result = database_transaction(
        connection_string="postgresql://user:pass@localhost/db",
        queries=queries
    )

    return result
```

**Parameters:**
- `connection_string` (str): Database connection string
- `queries` (list): List of query dictionaries
- `isolation_level` (str): Transaction isolation level

## Cloud Operations

### s3_operations

Interact with AWS S3 storage.

```python
from flux.tasks import s3_operations

@workflow
def s3_workflow():
    # Upload file
    s3_operations(
        operation="upload",
        bucket="my-bucket",
        key="data/file.txt",
        file_path="/local/path/file.txt"
    )

    # Download file
    s3_operations(
        operation="download",
        bucket="my-bucket",
        key="data/file.txt",
        file_path="/local/download/file.txt"
    )

    # List objects
    objects = s3_operations(
        operation="list",
        bucket="my-bucket",
        prefix="data/"
    )

    return objects
```

**Parameters:**
- `operation` (str): S3 operation ("upload", "download", "list", "delete")
- `bucket` (str): S3 bucket name
- `key` (str): S3 object key
- `file_path` (str): Local file path
- `prefix` (str): Object prefix for listing

### email_send

Send email notifications.

```python
from flux.tasks import email_send

@workflow
def notification_workflow(results: dict):
    email_send(
        to=["admin@example.com", "team@example.com"],
        subject="Workflow Completed Successfully",
        body=f"Workflow processed {results['count']} items",
        smtp_server="smtp.example.com",
        smtp_port=587,
        username="notifications@example.com",
        password="secret-password"
    )

    return "Notification sent"
```

**Parameters:**
- `to` (list): Recipient email addresses
- `subject` (str): Email subject
- `body` (str): Email body
- `cc` (list): CC recipients
- `bcc` (list): BCC recipients
- `smtp_server` (str): SMTP server hostname
- `smtp_port` (int): SMTP server port
- `username` (str): SMTP username
- `password` (str): SMTP password

## Utility Tasks

### log_message

Add structured logging to workflows.

```python
from flux.tasks import log_message

@workflow
def logging_workflow(data: dict):
    log_message(
        level="info",
        message="Starting data processing",
        extra={"data_size": len(data)}
    )

    try:
        # Process data
        result = process_data(data)

        log_message(
            level="info",
            message="Data processing completed",
            extra={"result_size": len(result)}
        )

        return result

    except Exception as e:
        log_message(
            level="error",
            message="Data processing failed",
            extra={"error": str(e)}
        )
        raise
```

**Parameters:**
- `level` (str): Log level ("debug", "info", "warning", "error", "critical")
- `message` (str): Log message
- `extra` (dict): Additional log context

### generate_uuid

Generate unique identifiers.

```python
from flux.tasks import generate_uuid

@workflow
def tracking_workflow():
    # Generate UUID4
    execution_id = generate_uuid()

    # Generate UUID with prefix
    task_id = generate_uuid(prefix="task")

    # Generate short UUID
    short_id = generate_uuid(short=True)

    return {
        "execution_id": execution_id,
        "task_id": task_id,
        "short_id": short_id
    }
```

**Parameters:**
- `version` (int): UUID version (1, 4)
- `prefix` (str): Prefix for UUID
- `short` (bool): Generate short UUID

### hash_data

Generate hashes for data integrity.

```python
from flux.tasks import hash_data

@workflow
def integrity_workflow(data: str):
    # Generate MD5 hash
    md5_hash = hash_data(data, algorithm="md5")

    # Generate SHA256 hash
    sha256_hash = hash_data(data, algorithm="sha256")

    # Hash file content
    file_hash = hash_data(
        file_path="/path/to/file.txt",
        algorithm="sha1"
    )

    return {
        "md5": md5_hash,
        "sha256": sha256_hash,
        "file_hash": file_hash
    }
```

**Parameters:**
- `data` (str/bytes): Data to hash
- `file_path` (str): Path to file for hashing
- `algorithm` (str): Hash algorithm ("md5", "sha1", "sha256", "sha512")

## Task Configuration

All built-in tasks support standard task configuration options:

```python
from flux.tasks import http_request

@workflow
def configured_http_workflow():
    # HTTP request with full configuration
    response = http_request(
        method="GET",
        url="https://api.example.com/data",
        headers={"Authorization": "Bearer token"},
        timeout=30,
        retries=3,
        retry_delay=5,

        # Task configuration
        task_name="fetch_api_data",
        task_timeout=60,
        task_priority=7,
        task_tags=["api", "external"]
    )

    return response
```

## Custom Built-in Tasks

You can extend the built-in task library by registering custom tasks:

```python
from flux.tasks import register_builtin_task

@register_builtin_task("custom_transform")
def custom_data_transform(data: list, transform_type: str) -> list:
    """Custom data transformation task."""
    if transform_type == "uppercase":
        return [item.upper() for item in data]
    elif transform_type == "reverse":
        return list(reversed(data))
    else:
        raise ValueError(f"Unknown transform type: {transform_type}")

# Usage in workflows
@workflow
def custom_workflow():
    from flux.tasks import custom_transform

    data = ["hello", "world"]
    result = custom_transform(data, transform_type="uppercase")
    return result  # ["HELLO", "WORLD"]
```

## Error Handling

Built-in tasks include comprehensive error handling:

```python
from flux.tasks import http_request
from flux.exceptions import TaskError, NetworkError

@workflow
def error_handling_workflow():
    try:
        response = http_request(
            method="GET",
            url="https://unreliable-api.com/data",
            retries=3,
            retry_delay=5
        )
        return response

    except NetworkError as e:
        # Handle network-specific errors
        log_message("error", f"Network error: {e}")
        return {"error": "network_failure"}

    except TaskError as e:
        # Handle general task errors
        log_message("error", f"Task error: {e}")
        return {"error": "task_failure"}
```

## See Also

- [Core Decorators](core-decorators.md) - Task and workflow decorators
- [Execution Context](execution-context.md) - Context management
- [Workflow Execution](workflow-execution.md) - Execution patterns
- [Task Configuration](../../user-guide/task-configuration.md) - Advanced configuration
