# Workflow Execution

This page documents workflow execution patterns, control flow, and advanced execution strategies in Flux.

## Overview

Flux provides flexible workflow execution patterns that support:

- **Sequential Execution**: Tasks run one after another
- **Parallel Execution**: Tasks run concurrently
- **Conditional Execution**: Tasks run based on conditions
- **Dynamic Workflows**: Runtime workflow modification
- **Error Recovery**: Sophisticated error handling and retry mechanisms

## Execution Patterns

### Sequential Execution

The simplest execution pattern where tasks run one after another.

```python
from flux import workflow, task, ExecutionContext

@task
async def fetch_data(source: str, context: ExecutionContext) -> dict:
    context.log_info(f"Fetching data from {source}")
    # Simulate data fetching
    return {"data": f"content_from_{source}", "source": source}

@task
async def validate_data(data: dict, context: ExecutionContext) -> dict:
    context.log_info("Validating data")
    # Validate data
    if not data.get("data"):
        raise ValueError("Invalid data")
    return {"validated": True, **data}

@task
async def process_data(data: dict, context: ExecutionContext) -> dict:
    context.log_info("Processing data")
    # Process data
    return {"processed": True, "result": data["data"].upper()}

@workflow
async def sequential_workflow(context: ExecutionContext[str]):
    """Sequential data processing workflow."""
    source = context.input

    # Step 1: Fetch data
    raw_data = await fetch_data(source, context)

    # Step 2: Validate data
    validated_data = await validate_data(raw_data, context)

    # Step 3: Process data
    final_result = await process_data(validated_data, context)

    return final_result
```

### Parallel Execution

Execute multiple tasks concurrently to improve performance.

```python
from flux.tasks import parallel

@task
async def fetch_source_a(context: ExecutionContext) -> dict:
    context.log_info("Fetching from source A")
    return {"source": "A", "data": "data_a"}

@task
async def fetch_source_b(context: ExecutionContext) -> dict:
    context.log_info("Fetching from source B")
    return {"source": "B", "data": "data_b"}

@task
async def fetch_source_c(context: ExecutionContext) -> dict:
    context.log_info("Fetching from source C")
    return {"source": "C", "data": "data_c"}

@task
async def merge_data(sources: list, context: ExecutionContext) -> dict:
    context.log_info(f"Merging data from {len(sources)} sources")
    merged = {"merged_data": [s["data"] for s in sources]}
    return merged

@workflow
async def parallel_workflow(context: ExecutionContext):
    """Parallel data fetching workflow."""

    # Execute tasks in parallel
    sources = await parallel(
        fetch_source_a(context),
        fetch_source_b(context),
        fetch_source_c(context)
    )

    # Merge results
        # Merge results
    final_result = await merge_data(sources, context)
    return final_result
```

### Mixed Parallel and Sequential

Combine parallel and sequential execution for complex workflows.

```python
@workflow
async def mixed_execution_workflow(context: ExecutionContext[list]):
    """Workflow with mixed execution patterns."""
    items = context.input

    # Phase 1: Parallel preprocessing
    context.log_info("Phase 1: Parallel preprocessing")
    preprocessed_items = await parallel(*[
        preprocess_item(item, context) for item in items
    ])

    # Phase 2: Sequential aggregation
    context.log_info("Phase 2: Sequential aggregation")
    aggregated = await aggregate_items(preprocessed_items, context)

    # Phase 3: Parallel postprocessing
    context.log_info("Phase 3: Parallel postprocessing")
    chunks = await split_into_chunks(aggregated, chunk_size=5)

    final_results = await parallel(*[
        postprocess_chunk(chunk, context) for chunk in chunks
    ])

    return {"results": final_results, "total_items": len(items)}
```

## Conditional Execution

### Basic Conditionals

Execute tasks based on runtime conditions.

```python
@task
async def check_user_permissions(user_id: str, context: ExecutionContext) -> dict:
    # Simulate permission check
    permissions = {"user_id": user_id, "is_admin": user_id == "admin"}
    context.log_info(f"User permissions: {permissions}")
    return permissions

@task
async def admin_task(context: ExecutionContext) -> str:
    context.log_info("Executing admin task")
    return "Admin task completed"

@task
async def regular_task(context: ExecutionContext) -> str:
    context.log_info("Executing regular task")
    return "Regular task completed"

@workflow
async def conditional_workflow(context: ExecutionContext[str]):
    """Workflow with conditional execution."""
    user_id = context.input

    # Check permissions
    permissions = await check_user_permissions(user_id, context)

    # Conditional execution
    if permissions["is_admin"]:
        result = await admin_task(context)
    else:
        result = await regular_task(context)

    return {"user": user_id, "result": result}
```

### Advanced Conditionals with Multiple Paths

```python
@workflow
async def multi_path_workflow(context: ExecutionContext[dict]):
    """Workflow with multiple conditional paths."""
    workflow_input = context.input
    data_type = workflow_input["type"]
    data = workflow_input["data"]

    context.log_info(f"Processing data type: {data_type}")

    if data_type == "json":
        # JSON processing path
        validated = await validate_json_data(data, context)
        processed = await process_json_data(validated, context)

    elif data_type == "xml":
        # XML processing path
        parsed = await parse_xml_data(data, context)
        validated = await validate_xml_data(parsed, context)
        processed = await process_xml_data(validated, context)

    elif data_type == "csv":
        # CSV processing path
        normalized = await normalize_csv_data(data, context)
        validated = await validate_csv_data(normalized, context)
        processed = await process_csv_data(validated, context)

    else:
        # Unsupported data type
        context.log_error(f"Unsupported data type: {data_type}")
        raise ValueError(f"Unsupported data type: {data_type}")

    # Common final processing
    final_result = await finalize_processing(processed, context)

    return {
        "data_type": data_type,
        "result": final_result,
        "processed_at": context.now().isoformat()
    }
```

## Dynamic Workflows

### Runtime Task Selection

Create workflows that adapt based on runtime data.

```python
@workflow
async def dynamic_task_workflow(context: ExecutionContext[dict]):
    """Workflow that selects tasks dynamically."""

    config = context.input
    enabled_tasks = config.get("enabled_tasks", [])
    results = {}

    # Task registry
    available_tasks = {
        "data_validation": validate_data_task,
        "data_enrichment": enrich_data_task,
        "data_export": export_data_task,
        "notification": send_notification_task
    }

    # Execute enabled tasks
    for task_name in enabled_tasks:
        if task_name in available_tasks:
            context.log_info(f"Executing dynamic task: {task_name}")
            task_func = available_tasks[task_name]
            result = await task_func(config, context)
            results[task_name] = result
        else:
            context.log_warning(f"Unknown task: {task_name}")

    return {
        "executed_tasks": list(results.keys()),
        "results": results
    }
```

### Dynamic Parallel Execution

Scale parallel execution based on workload.

```python
@workflow
async def dynamic_parallel_workflow(context: ExecutionContext[list]):
    """Workflow with dynamic parallelism."""

    items = context.input
    # Determine optimal worker count
    item_count = len(items)
    max_workers = min(item_count, 10)  # Cap at 10 workers

    if item_count < 5:
        # Sequential for small datasets
        context.log_info("Using sequential processing for small dataset")
        results = []
        for item in items:
            result = await process_item(item, context)
            results.append(result)
    else:
        # Parallel for larger datasets
        context.log_info(f"Using parallel processing with {max_workers} workers")
        results = await parallel([
            process_item(item, context) for item in items
        ])

    return {
        "item_count": item_count,
        "processing_mode": "parallel" if item_count >= 5 else "sequential",
        "results": results
    }
```

## Error Handling and Recovery

### Basic Error Handling

```python
@task
async def unreliable_task(context: ExecutionContext) -> str:
    """Task that might fail."""
    import random
    if random.random() < 0.3:  # 30% failure rate
        raise Exception("Random failure occurred")
    return "Task completed successfully"

@workflow
async def error_handling_workflow(context: ExecutionContext):
    """Workflow with error handling."""

    try:
        result = await unreliable_task(context)
        context.log_info(f"Task succeeded: {result}")
        return {"status": "success", "result": result}

    except Exception as e:
        context.log_error(f"Task failed: {e}")
        context.add_error(e, recoverable=True)

        # Fallback processing
        fallback_result = "Fallback result"
        return {"status": "fallback", "result": fallback_result}
```

### Advanced Error Recovery

```python
@task
async def critical_task(attempt: int, context: ExecutionContext) -> str:
    """Critical task with retry logic."""
    context.log_info(f"Critical task attempt {attempt}")

    # Simulate varying failure rates
    import random
    failure_rate = max(0.1, 0.8 - (attempt * 0.2))  # Decreasing failure rate

    if random.random() < failure_rate:
        raise Exception(f"Task failed on attempt {attempt}")

    return f"Success on attempt {attempt}"

@workflow
async def recovery_workflow(context: ExecutionContext):
    """Workflow with sophisticated error recovery."""

    max_attempts = 5

    for attempt in range(1, max_attempts + 1):
        try:
            result = await critical_task(attempt, context)
            context.log_info(f"Task succeeded on attempt {attempt}")
            return {
                "status": "success",
                "result": result,
                "attempts": attempt
            }

        except Exception as e:
            context.log_warning(f"Attempt {attempt} failed: {e}")
            context.add_error(e, recoverable=attempt < max_attempts)

            if attempt < max_attempts:
                # Exponential backoff
                delay = 2 ** attempt
                context.log_info(f"Retrying in {delay} seconds")
                import asyncio
                await asyncio.sleep(delay)
            else:
                # Final failure
                context.log_error("All attempts failed")
                return {
                    "status": "failed",
                    "attempts": attempt,
                    "final_error": str(e)
                }
```

### Partial Failure Handling

```python
@workflow
async def partial_failure_workflow(context: ExecutionContext[list]):
    """Handle partial failures in batch processing."""

    items = context.input
    successful_results = []
    failed_items = []

    # Process items with error handling
    results = await parallel([
        process_item_safely(item, i, context)
        for i, item in enumerate(items)
    ])

    # Separate successful and failed results
    for i, (item, result) in enumerate(zip(items, results)):
        if isinstance(result, Exception):
            context.log_error(f"Item {i} failed: {result}")
            failed_items.append({"index": i, "item": item, "error": str(result)})
            context.add_error(result, recoverable=True)
        else:
            successful_results.append(result)
            context.log_info(f"Item {i} processed successfully")

    # Summary
    total_items = len(items)
    success_count = len(successful_results)
    failure_count = len(failed_items)

    context.log_info(f"Batch processing complete: {success_count}/{total_items} successful")

    return {
        "total_items": total_items,
        "successful_count": success_count,
        "failed_count": failure_count,
        "success_rate": success_count / total_items if total_items > 0 else 0,
        "successful_results": successful_results,
        "failed_items": failed_items
    }

@task
async def process_item_safely(item: dict, index: int, context: ExecutionContext) -> dict:
    """Process item with built-in error context."""
    try:
        # Simulate processing with potential failure
        if "invalid" in str(item).lower():
            raise ValueError(f"Invalid item at index {index}")

        return {
            "index": index,
            "original": item,
            "processed": f"processed_{item}",
            "timestamp": context.now().isoformat()
        }

    except Exception as e:
        # Re-raise with context
        raise Exception(f"Failed to process item {index}: {e}") from e
```

## Workflow Composition

### Subworkflows

Break complex workflows into smaller, reusable components.

```python
@workflow
async def data_extraction_subworkflow(context: ExecutionContext[str]) -> dict:
    """Subworkflow for data extraction."""
    source = context.input
    context.log_info(f"Starting data extraction from {source}")

    # Connect to source
    connection = await connect_to_source(source, context)

    # Extract data
    raw_data = await extract_data(connection, context)

    # Clean up
    await cleanup_connection(connection, context)

    return {"source": source, "data": raw_data, "extracted_at": context.now()}

@workflow
async def data_transformation_subworkflow(context: ExecutionContext[dict]) -> dict:
    """Subworkflow for data transformation."""
    workflow_input = context.input
    data = workflow_input["data"]
    rules = workflow_input["rules"]

    context.log_info("Starting data transformation")

    transformed_data = data.copy()

    for rule in rules:
        transformed_data = await apply_transformation_rule(transformed_data, rule, context)

    return {"transformed_data": transformed_data, "applied_rules": len(rules)}

@workflow
async def main_etl_workflow(context: ExecutionContext[dict]):
    """Main ETL workflow composed of subworkflows."""

    workflow_input = context.input
    sources = workflow_input["sources"]
    transformation_rules = workflow_input["transformation_rules"]

    # Phase 1: Extract data from all sources in parallel
    context.log_info("Phase 1: Data extraction")
    extracted_data = await parallel([
        data_extraction_subworkflow(source, context)
        for source in sources
    ])

    # Phase 2: Transform data
    context.log_info("Phase 2: Data transformation")
    transformed_results = []
    for data in extracted_data:
        result = await data_transformation_subworkflow({
            "data": data,
            "rules": transformation_rules
        }, context)
        transformed_results.append(result)

    # Phase 3: Load data
    context.log_info("Phase 3: Data loading")
    load_result = await load_transformed_data(transformed_results, context)

    return {
        "sources_processed": len(sources),
        "transformation_rules_applied": len(transformation_rules),
        "load_result": load_result,
        "completed_at": context.now().isoformat()
    }
```

### Workflow Chaining

Chain multiple workflows together.

```python
@workflow
async def data_preparation_workflow(context: ExecutionContext[dict]) -> dict:
    """Prepare data for processing."""
    raw_input = context.input
    context.log_info("Starting data preparation")

    cleaned = await clean_data(raw_input, context)
    validated = await validate_data(cleaned, context)

    return {
        "prepared_data": validated,
        "preparation_metadata": {
            "timestamp": context.now().isoformat(),
            "source_size": len(str(raw_input)),
            "output_size": len(str(validated))
        }
    }

@workflow
async def data_processing_workflow(context: ExecutionContext[dict]) -> dict:
    """Process prepared data."""
    prepared_input = context.input
    context.log_info("Starting data processing")

    data = prepared_input["prepared_data"]

    # Process in chunks
    chunks = await split_data_into_chunks(data, chunk_size=100)

    processed_chunks = await parallel([
        process_data_chunk(chunk, context) for chunk in chunks
    ])

    final_result = await merge_processed_chunks(processed_chunks, context)

    return {
        "processed_data": final_result,
        "processing_metadata": {
            "chunks_processed": len(chunks),
            "timestamp": context.now().isoformat()
        }
    }

@workflow
async def complete_pipeline_workflow(context: ExecutionContext[dict]):
    """Complete pipeline chaining multiple workflows."""

    raw_input = context.input

    # Step 1: Prepare data
    preparation_result = await data_preparation_workflow(raw_input, context)

    # Step 2: Process data
    processing_result = await data_processing_workflow(preparation_result, context)

    # Step 3: Finalize
    final_output = await finalize_results(processing_result, context)

    return {
        "pipeline_result": final_output,
        "metadata": {
            "preparation": preparation_result.get("preparation_metadata"),
            "processing": processing_result.get("processing_metadata"),
            "completed_at": context.now().isoformat()
        }
    }
```

## Execution Control

### Timeouts and Cancellation

```python
@workflow
async def timeout_aware_workflow(context: ExecutionContext):
    """Workflow with timeout handling."""

    # Set workflow timeout
    workflow_timeout = 300  # 5 minutes
    start_time = context.now()

    items_to_process = await get_items_to_process()
    processed_items = []

    for item in items_to_process:
        # Check timeout before processing each item
        elapsed = (context.now() - start_time).total_seconds()
        if elapsed >= workflow_timeout:
            context.log_warning("Workflow timeout reached, stopping gracefully")
            break

        # Check for cancellation signal
        if context.should_stop():
            context.log_info("Cancellation requested, stopping gracefully")
            break

        # Process item with remaining time
        remaining_time = workflow_timeout - elapsed
        try:
            result = await process_item_with_timeout(item, remaining_time, context)
            processed_items.append(result)
        except TimeoutError:
            context.log_warning(f"Item processing timed out: {item}")
            break

    return {
        "processed_count": len(processed_items),
        "total_count": len(items_to_process),
        "execution_time": (context.now() - start_time).total_seconds()
    }
```

### Resource Management

```python
@workflow
async def resource_managed_workflow(context: ExecutionContext):
    """Workflow with resource management."""

    # Acquire resources
    database_pool = await acquire_database_connection_pool(context)
    file_handles = await acquire_file_handles(context)

    try:
        # Use resources
        results = await parallel([
            process_with_resources(i, database_pool, file_handles, context)
            for i in range(10)
        ])

        return {"results": results, "status": "success"}

    finally:
        # Always clean up resources
        await release_file_handles(file_handles, context)
        await release_database_connection_pool(database_pool, context)
        context.log_info("Resources released successfully")
```

## See Also

- [Core Decorators](core-decorators.md) - Task and workflow decorators
- [Built-in Tasks](built-in-tasks.md) - Pre-built task implementations
- [Execution Context](execution-context.md) - Context management
- [Workflow Patterns](../../user-guide/workflow-patterns.md) - Common workflow patterns
