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
def fetch_data(source: str, context: ExecutionContext) -> dict:
    context.log_info(f"Fetching data from {source}")
    # Simulate data fetching
    return {"data": f"content_from_{source}", "source": source}

@task
def validate_data(data: dict, context: ExecutionContext) -> dict:
    context.log_info("Validating data")
    # Validate data
    if not data.get("data"):
        raise ValueError("Invalid data")
    return {"validated": True, **data}

@task
def process_data(data: dict, context: ExecutionContext) -> dict:
    context.log_info("Processing data")
    # Process data
    return {"processed": True, "result": data["data"].upper()}

@workflow
def sequential_workflow(source: str, context: ExecutionContext):
    """Sequential data processing workflow."""
    # Step 1: Fetch data
    raw_data = fetch_data(source, context)

    # Step 2: Validate data
    validated_data = validate_data(raw_data, context)

    # Step 3: Process data
    final_result = process_data(validated_data, context)

    return final_result
```

### Parallel Execution

Execute multiple tasks concurrently to improve performance.

```python
@task
def fetch_source_a(context: ExecutionContext) -> dict:
    context.log_info("Fetching from source A")
    return {"source": "A", "data": "data_a"}

@task
def fetch_source_b(context: ExecutionContext) -> dict:
    context.log_info("Fetching from source B")
    return {"source": "B", "data": "data_b"}

@task
def fetch_source_c(context: ExecutionContext) -> dict:
    context.log_info("Fetching from source C")
    return {"source": "C", "data": "data_c"}

@task
def merge_data(sources: list, context: ExecutionContext) -> dict:
    context.log_info(f"Merging data from {len(sources)} sources")
    merged = {"merged_data": [s["data"] for s in sources]}
    return merged

@workflow
def parallel_workflow(context: ExecutionContext):
    """Parallel data fetching workflow."""

    # Execute tasks in parallel
    with context.parallel(max_workers=3) as parallel:
        future_a = parallel.submit(fetch_source_a, context)
        future_b = parallel.submit(fetch_source_b, context)
        future_c = parallel.submit(fetch_source_c, context)

    # Collect results
    sources = [
        future_a.result(),
        future_b.result(),
        future_c.result()
    ]

    # Merge results
    final_result = merge_data(sources, context)
    return final_result
```

### Mixed Parallel and Sequential

Combine parallel and sequential execution for complex workflows.

```python
@workflow
def mixed_execution_workflow(items: list, context: ExecutionContext):
    """Workflow with mixed execution patterns."""

    # Phase 1: Parallel preprocessing
    context.log_info("Phase 1: Parallel preprocessing")
    with context.parallel(max_workers=4) as parallel:
        preprocessed_futures = []
        for item in items:
            future = parallel.submit(preprocess_item, item, context)
            preprocessed_futures.append(future)

    preprocessed_items = [f.result() for f in preprocessed_futures]

    # Phase 2: Sequential aggregation
    context.log_info("Phase 2: Sequential aggregation")
    aggregated = aggregate_items(preprocessed_items, context)

    # Phase 3: Parallel postprocessing
    context.log_info("Phase 3: Parallel postprocessing")
    chunks = split_into_chunks(aggregated, chunk_size=5)

    with context.parallel(max_workers=2) as parallel:
        postprocessed_futures = []
        for chunk in chunks:
            future = parallel.submit(postprocess_chunk, chunk, context)
            postprocessed_futures.append(future)

    final_results = [f.result() for f in postprocessed_futures]

    return {"results": final_results, "total_items": len(items)}
```

## Conditional Execution

### Basic Conditionals

Execute tasks based on runtime conditions.

```python
@task
def check_user_permissions(user_id: str, context: ExecutionContext) -> dict:
    # Simulate permission check
    permissions = {"user_id": user_id, "is_admin": user_id == "admin"}
    context.log_info(f"User permissions: {permissions}")
    return permissions

@task
def admin_task(context: ExecutionContext) -> str:
    context.log_info("Executing admin task")
    return "Admin task completed"

@task
def regular_task(context: ExecutionContext) -> str:
    context.log_info("Executing regular task")
    return "Regular task completed"

@workflow
def conditional_workflow(user_id: str, context: ExecutionContext):
    """Workflow with conditional execution."""

    # Check permissions
    permissions = check_user_permissions(user_id, context)

    # Conditional execution
    if permissions["is_admin"]:
        result = admin_task(context)
    else:
        result = regular_task(context)

    return {"user": user_id, "result": result}
```

### Advanced Conditionals with Multiple Paths

```python
@workflow
def multi_path_workflow(data_type: str, data: dict, context: ExecutionContext):
    """Workflow with multiple conditional paths."""

    context.log_info(f"Processing data type: {data_type}")

    if data_type == "json":
        # JSON processing path
        validated = validate_json_data(data, context)
        processed = process_json_data(validated, context)

    elif data_type == "xml":
        # XML processing path
        parsed = parse_xml_data(data, context)
        validated = validate_xml_data(parsed, context)
        processed = process_xml_data(validated, context)

    elif data_type == "csv":
        # CSV processing path
        normalized = normalize_csv_data(data, context)
        validated = validate_csv_data(normalized, context)
        processed = process_csv_data(validated, context)

    else:
        # Unsupported data type
        context.log_error(f"Unsupported data type: {data_type}")
        raise ValueError(f"Unsupported data type: {data_type}")

    # Common final processing
    final_result = finalize_processing(processed, context)

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
def dynamic_task_workflow(config: dict, context: ExecutionContext):
    """Workflow that selects tasks dynamically."""

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
            result = task_func(config, context)
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
def dynamic_parallel_workflow(items: list, context: ExecutionContext):
    """Workflow with dynamic parallelism."""

    # Determine optimal worker count
    item_count = len(items)
    max_workers = min(item_count, 10)  # Cap at 10 workers

    if item_count < 5:
        # Sequential for small datasets
        context.log_info("Using sequential processing for small dataset")
        results = []
        for item in items:
            result = process_item(item, context)
            results.append(result)
    else:
        # Parallel for larger datasets
        context.log_info(f"Using parallel processing with {max_workers} workers")
        with context.parallel(max_workers=max_workers) as parallel:
            futures = []
            for item in items:
                future = parallel.submit(process_item, item, context)
                futures.append(future)

            results = [f.result() for f in futures]

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
def unreliable_task(context: ExecutionContext) -> str:
    """Task that might fail."""
    import random
    if random.random() < 0.3:  # 30% failure rate
        raise Exception("Random failure occurred")
    return "Task completed successfully"

@workflow
def error_handling_workflow(context: ExecutionContext):
    """Workflow with error handling."""

    try:
        result = unreliable_task(context)
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
def critical_task(attempt: int, context: ExecutionContext) -> str:
    """Critical task with retry logic."""
    context.log_info(f"Critical task attempt {attempt}")

    # Simulate varying failure rates
    import random
    failure_rate = max(0.1, 0.8 - (attempt * 0.2))  # Decreasing failure rate

    if random.random() < failure_rate:
        raise Exception(f"Task failed on attempt {attempt}")

    return f"Success on attempt {attempt}"

@workflow
def recovery_workflow(context: ExecutionContext):
    """Workflow with sophisticated error recovery."""

    max_attempts = 5

    for attempt in range(1, max_attempts + 1):
        try:
            result = critical_task(attempt, context)
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
                time.sleep(delay)
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
def partial_failure_workflow(items: list, context: ExecutionContext):
    """Handle partial failures in batch processing."""

    successful_results = []
    failed_items = []

    with context.parallel(max_workers=4) as parallel:
        # Submit all tasks
        futures = []
        for i, item in enumerate(items):
            future = parallel.submit(process_item_safely, item, i, context)
            futures.append((i, item, future))

        # Collect results, handling failures
        for i, item, future in futures:
            try:
                result = future.result(timeout=60)
                successful_results.append(result)
                context.log_info(f"Item {i} processed successfully")

            except Exception as e:
                context.log_error(f"Item {i} failed: {e}")
                failed_items.append({"index": i, "item": item, "error": str(e)})
                context.add_error(e, recoverable=True)

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
def process_item_safely(item: dict, index: int, context: ExecutionContext) -> dict:
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
def data_extraction_subworkflow(source: str, context: ExecutionContext) -> dict:
    """Subworkflow for data extraction."""
    context.log_info(f"Starting data extraction from {source}")

    # Connect to source
    connection = connect_to_source(source, context)

    # Extract data
    raw_data = extract_data(connection, context)

    # Clean up
    cleanup_connection(connection, context)

    return {"source": source, "data": raw_data, "extracted_at": context.now()}

@workflow
def data_transformation_subworkflow(data: dict, rules: list, context: ExecutionContext) -> dict:
    """Subworkflow for data transformation."""
    context.log_info("Starting data transformation")

    transformed_data = data.copy()

    for rule in rules:
        transformed_data = apply_transformation_rule(transformed_data, rule, context)

    return {"transformed_data": transformed_data, "applied_rules": len(rules)}

@workflow
def main_etl_workflow(sources: list, transformation_rules: list, context: ExecutionContext):
    """Main ETL workflow composed of subworkflows."""

    # Phase 1: Extract data from all sources in parallel
    context.log_info("Phase 1: Data extraction")
    with context.parallel(max_workers=len(sources)) as parallel:
        extraction_futures = []
        for source in sources:
            future = parallel.submit(data_extraction_subworkflow, source, context)
            extraction_futures.append(future)

    extracted_data = [f.result() for f in extraction_futures]

    # Phase 2: Transform data
    context.log_info("Phase 2: Data transformation")
    transformed_results = []
    for data in extracted_data:
        result = data_transformation_subworkflow(data, transformation_rules, context)
        transformed_results.append(result)

    # Phase 3: Load data
    context.log_info("Phase 3: Data loading")
    load_result = load_transformed_data(transformed_results, context)

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
def data_preparation_workflow(raw_input: dict, context: ExecutionContext) -> dict:
    """Prepare data for processing."""
    context.log_info("Starting data preparation")

    cleaned = clean_data(raw_input, context)
    validated = validate_data(cleaned, context)

    return {
        "prepared_data": validated,
        "preparation_metadata": {
            "timestamp": context.now().isoformat(),
            "source_size": len(str(raw_input)),
            "output_size": len(str(validated))
        }
    }

@workflow
def data_processing_workflow(prepared_input: dict, context: ExecutionContext) -> dict:
    """Process prepared data."""
    context.log_info("Starting data processing")

    data = prepared_input["prepared_data"]

    # Process in chunks
    chunks = split_data_into_chunks(data, chunk_size=100)

    with context.parallel(max_workers=4) as parallel:
        chunk_futures = []
        for chunk in chunks:
            future = parallel.submit(process_data_chunk, chunk, context)
            chunk_futures.append(future)

    processed_chunks = [f.result() for f in chunk_futures]
    final_result = merge_processed_chunks(processed_chunks, context)

    return {
        "processed_data": final_result,
        "processing_metadata": {
            "chunks_processed": len(chunks),
            "timestamp": context.now().isoformat()
        }
    }

@workflow
def complete_pipeline_workflow(raw_input: dict, context: ExecutionContext):
    """Complete pipeline chaining multiple workflows."""

    # Step 1: Prepare data
    preparation_result = data_preparation_workflow(raw_input, context)

    # Step 2: Process data
    processing_result = data_processing_workflow(preparation_result, context)

    # Step 3: Finalize
    final_output = finalize_results(processing_result, context)

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
def timeout_aware_workflow(context: ExecutionContext):
    """Workflow with timeout handling."""

    # Set workflow timeout
    workflow_timeout = 300  # 5 minutes
    start_time = context.now()

    items_to_process = get_items_to_process()
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
            result = process_item_with_timeout(item, remaining_time, context)
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
def resource_managed_workflow(context: ExecutionContext):
    """Workflow with resource management."""

    # Acquire resources
    database_pool = acquire_database_connection_pool(context)
    file_handles = acquire_file_handles(context)

    try:
        # Use resources
        with context.parallel(max_workers=4) as parallel:
            futures = []
            for i in range(10):
                future = parallel.submit(
                    process_with_resources,
                    i,
                    database_pool,
                    file_handles,
                    context
                )
                futures.append(future)

            results = [f.result() for f in futures]

        return {"results": results, "status": "success"}

    finally:
        # Always clean up resources
        release_file_handles(file_handles, context)
        release_database_connection_pool(database_pool, context)
        context.log_info("Resources released successfully")
```

## See Also

- [Core Decorators](core-decorators.md) - Task and workflow decorators
- [Built-in Tasks](built-in-tasks.md) - Pre-built task implementations
- [Execution Context](execution-context.md) - Context management
- [Workflow Patterns](../../user-guide/workflow-patterns.md) - Common workflow patterns
