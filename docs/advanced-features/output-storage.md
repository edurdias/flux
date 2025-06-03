# Output Storage

Flux provides flexible output storage mechanisms to persist workflow and task results, enabling durability, resumability, and result sharing across different execution contexts.

## Overview

Output storage in Flux allows you to:

- **Persist Results**: Store workflow and task outputs for later retrieval
- **Enable Resumability**: Continue from previous execution states
- **Share Data**: Access results across different workflow runs
- **Optimize Performance**: Cache expensive computations
- **Support Large Outputs**: Handle data that exceeds memory limits

## Storage Types

Flux provides built-in storage implementations for different use cases:

### Inline Storage

The simplest storage option that keeps data in memory within the execution context.

```python
from flux import workflow, task, ExecutionContext
from flux.output_storage import InlineOutputStorage

# Configure inline storage
inline_storage = InlineOutputStorage()

@task.with_options(output_storage=inline_storage)
async def process_data(data: str):
    return data.upper()

@workflow.with_options(output_storage=inline_storage)
async def inline_workflow(ctx: ExecutionContext[str]):
    result = await process_data(ctx.input)
    return result
```

**Characteristics:**
- Results stored directly in execution context metadata
- Best for small data that fits in memory
- No external dependencies required
- Data persisted as part of execution state

### Local File Storage

Stores outputs as files on the local filesystem with configurable serialization.

```python
from flux.output_storage import LocalFileStorage

# Configure file storage
file_storage = LocalFileStorage()

@task.with_options(output_storage=file_storage)
async def load_dataset(filename: str):
    import pandas as pd
    df = pd.read_csv(filename)
    return df

@workflow.with_options(output_storage=file_storage)
async def file_workflow(ctx: ExecutionContext[str]):
    data = await load_dataset(ctx.input)
    return data
```

**Configuration Options:**
- **Base Path**: Configured via `FLUX_LOCAL_STORAGE_PATH` (default: `.data/storage`)
- **Serialization**: JSON or Pickle via `FLUX_SERIALIZER` (default: `json`)
- **File Naming**: Automatic based on execution ID and reference ID

**Characteristics:**
- Supports large datasets that don't fit in memory
- Configurable serialization (JSON for simple data, Pickle for complex objects)
- Files automatically organized by execution context
- Supports data sharing between workflow runs

## Configuration

### Environment Variables

```bash
# Storage configuration
export FLUX_LOCAL_STORAGE_PATH="/path/to/storage"
export FLUX_SERIALIZER="json"  # or "pkl" for pickle

# Database configuration (affects storage references)
export FLUX_DATABASE_URL="sqlite:///path/to/database.db"
```

### Programmatic Configuration

```python
from flux.config import Configuration

# Update storage settings
config = Configuration.get()
config.settings.local_storage_path = "/custom/storage/path"
config.settings.serializer = "pkl"
```

## Working with Storage References

When output storage is configured, results are returned as `OutputStorageReference` objects instead of direct values.

### Storage Reference Structure

```python
from flux.output_storage import OutputStorageReference

# Example reference
reference = OutputStorageReference(
    storage_type="local_file",           # Storage implementation type
    reference_id="task_12345",           # Unique reference identifier
    metadata={                           # Storage-specific metadata
        "serializer": "json",
        "file_path": "/path/to/file.json"
    }
)
```

### Retrieving Stored Values

```python
# Access stored value through storage
stored_value = file_storage.retrieve(reference)

# Or access directly from workflow output if it's a reference
ctx = my_workflow.run("input_data")
if isinstance(ctx.output, OutputStorageReference):
    actual_output = file_storage.retrieve(ctx.output)
else:
    actual_output = ctx.output
```

## Advanced Usage

### Custom Storage Implementation

Create custom storage backends by extending the `OutputStorage` abstract base class:

```python
from flux.output_storage import OutputStorage, OutputStorageReference
from typing import Any

class DatabaseOutputStorage(OutputStorage):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    def store(self, reference_id: str, value: Any) -> OutputStorageReference:
        # Store value in database
        # Return reference with database-specific metadata
        return OutputStorageReference(
            storage_type="database",
            reference_id=reference_id,
            metadata={"table": "results", "row_id": stored_id}
        )

    def retrieve(self, reference: OutputStorageReference) -> Any:
        # Retrieve value from database using reference metadata
        return retrieved_value

    def delete(self, reference: OutputStorageReference):
        # Clean up stored value
        pass
```

### Conditional Storage

Apply storage only to specific tasks or workflows based on data size or type:

```python
def smart_storage_factory(data):
    """Choose storage based on data characteristics."""
    if isinstance(data, pd.DataFrame) and len(data) > 10000:
        return LocalFileStorage()
    else:
        return InlineOutputStorage()

@task
async def adaptive_task(data):
    storage = smart_storage_factory(data)
    # Configure task with appropriate storage
    return processed_data
```

### Storage with Caching

Combine output storage with task caching for optimal performance:

```python
@task.with_options(
    cache=True,                    # Enable result caching
    output_storage=file_storage    # Store large results to disk
)
async def expensive_computation(input_data):
    # Expensive operation with large output
    result = perform_heavy_calculation(input_data)
    return result
```

## Integration Examples

### Data Pipeline with Mixed Storage

```python
@workflow.with_options(output_storage=file_storage)
async def data_pipeline(ctx: ExecutionContext[str]):
    # Load raw data (stored to file)
    raw_data = await load_raw_data(ctx.input)

    # Process data (inline storage for metadata)
    @task.with_options(output_storage=inline_storage)
    async def extract_metadata(data_ref):
        data = file_storage.retrieve(data_ref)
        return {"rows": len(data), "columns": list(data.columns)}

    metadata = await extract_metadata(raw_data)

    # Transform data (back to file storage)
    processed_data = await transform_data(raw_data)

    return {
        "data": processed_data,      # File reference
        "metadata": metadata         # Inline data
    }
```

### Cross-Workflow Data Sharing

```python
# Producer workflow
@workflow.with_options(output_storage=file_storage)
async def data_producer(ctx: ExecutionContext):
    large_dataset = await generate_dataset()
    return large_dataset

# Consumer workflow
@workflow
async def data_consumer(ctx: ExecutionContext[OutputStorageReference]):
    # Retrieve data from storage reference
    dataset = file_storage.retrieve(ctx.input)
    analysis = await analyze_dataset(dataset)
    return analysis

# Usage
producer_ctx = data_producer.run()
consumer_ctx = data_consumer.run(producer_ctx.output)
```

## Best Practices

### Storage Selection Guidelines

1. **Inline Storage**
   - Small results (< 1MB)
   - Simple data types (strings, numbers, small dictionaries)
   - When data doesn't need to be shared

2. **File Storage**
   - Large datasets or complex objects
   - When results need to persist beyond workflow execution
   - For sharing data between workflows

### Performance Considerations

```python
# Efficient: Store large intermediate results
@task.with_options(output_storage=file_storage)
async def preprocess_large_dataset(data):
    # Avoid keeping large data in memory
    return processed_large_data

# Efficient: Use inline storage for small metadata
@task.with_options(output_storage=inline_storage)
async def extract_summary(data_ref):
    data = file_storage.retrieve(data_ref)
    return {"count": len(data), "avg": data.mean()}
```

### Error Handling

```python
@workflow
async def robust_storage_workflow(ctx: ExecutionContext):
    try:
        result = await process_data(ctx.input)
        return result
    except Exception as e:
        # Handle storage errors gracefully
        if isinstance(e, FileNotFoundError):
            # Storage file missing
            return {"error": "Data not found", "retry": True}
        raise
```

### Cleanup and Maintenance

```python
# Cleanup old storage files
def cleanup_old_files(storage: LocalFileStorage, days_old: int = 30):
    """Remove storage files older than specified days."""
    import os
    import time

    current_time = time.time()
    for file_path in storage.base_path.glob("*.json"):
        if current_time - os.path.getctime(file_path) > days_old * 86400:
            file_path.unlink()
```

## Troubleshooting

### Common Issues

**Storage Reference Not Found**
```python
# Check if reference exists before retrieval
try:
    result = storage.retrieve(reference)
except FileNotFoundError:
    # Handle missing data
    result = fallback_value
```

**Serialization Errors**
```python
# Use pickle for complex objects that can't be JSON-serialized
@task.with_options(output_storage=LocalFileStorage())  # Uses pkl by default
async def complex_task():
    return ComplexObject()  # Custom class, lambda, etc.
```

**Storage Path Issues**
```python
# Ensure storage directory exists and is writable
import os
storage_path = "/path/to/storage"
os.makedirs(storage_path, exist_ok=True)
```

For more information about storage integration with workflows and tasks, see the [Workflow Management](../core-concepts/workflow-management.md) and [Task System](../core-concepts/tasks.md) documentation.
