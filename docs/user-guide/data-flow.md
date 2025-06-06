# Data Flow and State Management

Understanding how data flows through Flux workflows and how state is managed is crucial for building robust, scalable workflows. This guide covers the fundamentals of data passing, state persistence, and memory management in Flux.

## What You'll Learn

This guide covers:
- Execution context usage and data access patterns
- State persistence mechanisms and automatic checkpointing
- Data passing strategies between tasks
- Memory management and performance optimization
- Best practices for large-scale data workflows

## Execution Context as State Container

The `ExecutionContext` serves as the central state container for all workflow data and metadata.

### Basic Data Access

```python
from flux import workflow, task, ExecutionContext

@task
async def process_data(data: dict) -> dict:
    """Process input data and return transformed result."""
    return {
        "processed": True,
        "timestamp": await now(),
        "original_size": len(str(data)),
        "processed_data": data["content"].upper()
    }

@workflow
async def data_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Demonstrates basic data flow patterns."""

    # Access input data through context
    input_data = ctx.input
    print(f"Processing workflow {ctx.execution_id} with input: {input_data}")

    # Pass data to tasks
    result = await process_data(input_data)

    # The return value becomes ctx.output
    return {
        "status": "completed",
        "execution_id": ctx.execution_id,
        "result": result
    }
```

### Context Properties for Data Management

```python
@workflow
async def context_data_example(ctx: ExecutionContext[str]) -> dict:
    """Demonstrates various context properties for data management."""

    return {
        # Execution identification
        "execution_id": ctx.execution_id,
        "workflow_name": ctx.workflow_name,
        "workflow_id": ctx.workflow_id,

        # Data access
        "input": ctx.input,
        "output": ctx.output,  # None during execution, populated after completion

        # State information
        "state": ctx.state.value,
        "has_finished": ctx.has_finished,
        "has_succeeded": ctx.has_succeeded,
        "is_paused": ctx.is_paused,

        # Worker information
        "current_worker": ctx.current_worker,

        # Event tracking
        "event_count": len(ctx.events),
        "last_event": ctx.events[-1].type.value if ctx.events else None
    }
```

## State Persistence

Flux automatically persists workflow state to ensure durability and fault tolerance.

### Automatic Checkpointing

State is automatically saved at key points during execution:

```python
@task
async def cpu_intensive_task(data: list) -> list:
    """Simulate CPU-intensive processing."""
    await sleep(2)  # Simulate processing time
    return [x * 2 for x in data]

@task
async def io_intensive_task(data: list) -> dict:
    """Simulate I/O-intensive processing."""
    await sleep(1)  # Simulate I/O wait
    return {"processed_count": len(data), "sum": sum(data)}

@workflow
async def persistent_workflow(ctx: ExecutionContext[list]) -> dict:
    """Workflow with automatic state persistence."""

    # Checkpoint saved after each task completion
    step1_result = await cpu_intensive_task(ctx.input)
    # State automatically saved here

    step2_result = await io_intensive_task(step1_result)
    # State automatically saved here

    # Final state saved when workflow completes
    return {
        "cpu_result": step1_result,
        "io_result": step2_result,
        "total_processing_time": "3 seconds"
    }
```

### Manual Checkpointing

For fine-grained control, you can trigger manual checkpoints:

```python
@workflow
async def manual_checkpoint_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow with manual checkpoint control."""

    batch_size = 100
    total_items = len(ctx.input["items"])
    results = []

    for i in range(0, total_items, batch_size):
        batch = ctx.input["items"][i:i + batch_size]
        batch_result = await process_batch(batch)
        results.extend(batch_result)

        # Manual checkpoint after each batch
        if hasattr(ctx, '_checkpoint'):
            await ctx.checkpoint()
            print(f"Checkpoint saved after processing batch {i // batch_size + 1}")

    return {"processed_items": len(results), "results": results}
```

### Recovery and Replay

When a workflow is interrupted, Flux can recover from the last checkpoint:

```python
# Example of workflow recovery
@workflow
async def recovery_example(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that demonstrates recovery capabilities."""

    # Check if this is a resumed execution
    if ctx.has_resumed:
        print(f"Resuming workflow {ctx.execution_id} from last checkpoint")

        # Access events to understand what was already completed
        completed_tasks = [
            e for e in ctx.events
            if e.type == ExecutionEventType.TASK_COMPLETED
        ]
        print(f"Already completed {len(completed_tasks)} tasks")

    # Continue with normal workflow logic
    result = await multi_step_process(ctx.input)
    return result

# Original execution
ctx = recovery_example.run({"data": "large_dataset"})

# If interrupted, replay with the same execution_id
# Only missing steps will be executed
recovered_ctx = recovery_example.run(execution_id=ctx.execution_id)
```

## Data Passing Between Tasks

### Direct Data Passing

The most straightforward pattern is passing data directly between tasks:

```python
@task
async def extract_data(source: str) -> list:
    """Extract data from source."""
    return [1, 2, 3, 4, 5]

@task
async def transform_data(data: list) -> list:
    """Transform the extracted data."""
    return [x * 2 for x in data]

@task
async def load_data(data: list, destination: str) -> dict:
    """Load transformed data to destination."""
    return {
        "loaded_count": len(data),
        "destination": destination,
        "checksum": sum(data)
    }

@workflow
async def etl_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Extract, Transform, Load workflow with direct data passing."""

    # Extract
    raw_data = await extract_data(ctx.input["source"])

    # Transform
    transformed_data = await transform_data(raw_data)

    # Load
    result = await load_data(transformed_data, ctx.input["destination"])

    return result
```

### Context-Based Data Sharing

For complex workflows, you can use the context to share data:

```python
@task
async def initialize_state(config: dict) -> dict:
    """Initialize workflow state."""
    return {
        "initialized_at": await now(),
        "config": config,
        "stage": "initialized"
    }

@task
async def process_stage_one(state: dict, input_data: any) -> dict:
    """Process first stage and update state."""
    processed = await complex_processing(input_data)
    return {
        **state,
        "stage": "stage_one_complete",
        "stage_one_result": processed,
        "stage_one_completed_at": await now()
    }

@task
async def process_stage_two(state: dict) -> dict:
    """Process second stage using state from previous stage."""
    # Use result from stage one
    stage_one_data = state["stage_one_result"]
    final_result = await final_processing(stage_one_data)

    return {
        **state,
        "stage": "completed",
        "final_result": final_result,
        "completed_at": await now()
    }

@workflow
async def stateful_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that maintains state across multiple stages."""

    # Initialize workflow state
    state = await initialize_state(ctx.input["config"])

    # Process stages, maintaining state
    state = await process_stage_one(state, ctx.input["data"])
    state = await process_stage_two(state)

    return state
```

## Memory Management

### Efficient Data Handling

For large datasets, consider memory-efficient patterns:

```python
@task
async def process_large_dataset(data_source: str, batch_size: int = 1000) -> dict:
    """Process large dataset in batches to manage memory."""

    total_processed = 0
    results_summary = {"batches": 0, "total_items": 0, "errors": 0}

    # Process in batches to avoid memory issues
    for batch in stream_data_batches(data_source, batch_size):
        try:
            batch_result = await process_batch(batch)
            total_processed += len(batch_result)
            results_summary["batches"] += 1
            results_summary["total_items"] += len(batch)
        except Exception as e:
            results_summary["errors"] += 1
            # Log error but continue processing
            print(f"Error processing batch: {e}")

    return results_summary

@workflow
async def large_data_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow optimized for large data processing."""

    # Process large dataset efficiently
    summary = await process_large_dataset(
        ctx.input["data_source"],
        ctx.input.get("batch_size", 1000)
    )

    # Return summary instead of full data to save memory
    return {
        "processing_summary": summary,
        "execution_id": ctx.execution_id,
        "processed_at": await now()
    }
```

### Output Storage for Large Results

Use output storage for large results instead of keeping them in memory:

```python
from flux.output_storage import OutputStorage, LocalFileStorage

@task.with_options(
    output_storage=LocalFileStorage(base_path="./workflow_outputs")
)
async def generate_large_report(data: dict) -> str:
    """Generate large report and store it externally."""

    # Generate large report content
    report_content = await generate_comprehensive_report(data)

    # Return reference to stored content
    return report_content

@workflow
async def reporting_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that generates large reports using output storage."""

    # Generate report (stored externally)
    report_ref = await generate_large_report(ctx.input)

    # Return metadata instead of full content
    return {
        "report_generated": True,
        "report_reference": report_ref,
        "execution_id": ctx.execution_id,
        "generated_at": await now()
    }
```

## Advanced Data Flow Patterns

### Pipeline Processing

Chain multiple transformations in a pipeline:

```python
@task
async def validate_input(data: dict) -> dict:
    """Validate input data."""
    if not data.get("required_field"):
        raise ValueError("Missing required field")
    return {"validated": True, **data}

@task
async def enrich_data(data: dict) -> dict:
    """Enrich data with additional information."""
    enriched = await fetch_enrichment_data(data["id"])
    return {**data, "enrichment": enriched}

@task
async def normalize_data(data: dict) -> dict:
    """Normalize data format."""
    return {
        "id": data["id"],
        "normalized_value": normalize_value(data["value"]),
        "enrichment": data["enrichment"],
        "processed_at": await now()
    }

@workflow
async def data_pipeline(ctx: ExecutionContext[dict]) -> dict:
    """Data processing pipeline with validation, enrichment, and normalization."""

    # Create processing pipeline
    validated_data = await validate_input(ctx.input)
    enriched_data = await enrich_data(validated_data)
    normalized_data = await normalize_data(enriched_data)

    return {
        "pipeline_result": normalized_data,
        "stages_completed": ["validation", "enrichment", "normalization"]
    }
```

### Conditional Data Flow

Implement conditional logic based on data content:

```python
@task
async def analyze_data(data: dict) -> dict:
    """Analyze data and determine processing path."""

    data_size = len(str(data))
    complexity = calculate_complexity(data)

    return {
        "size": data_size,
        "complexity": complexity,
        "processing_path": "complex" if complexity > 0.7 else "simple",
        "original_data": data
    }

@task
async def simple_processing(analysis: dict) -> dict:
    """Simple processing for straightforward data."""
    await sleep(1)  # Simulate simple processing
    return {"result": "simple_processed", "analysis": analysis}

@task
async def complex_processing(analysis: dict) -> dict:
    """Complex processing for sophisticated data."""
    await sleep(3)  # Simulate complex processing
    return {"result": "complex_processed", "analysis": analysis}

@workflow
async def conditional_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow with conditional data flow based on analysis."""

    # Analyze input to determine processing path
    analysis = await analyze_data(ctx.input)

    # Conditional processing based on analysis
    if analysis["processing_path"] == "complex":
        result = await complex_processing(analysis)
    else:
        result = await simple_processing(analysis)

    return {
        "processing_path": analysis["processing_path"],
        "result": result,
        "execution_metadata": {
            "execution_id": ctx.execution_id,
            "workflow_name": ctx.workflow_name
        }
    }
```

## Best Practices

### 1. Use Type Hints for Data Flow Clarity

```python
from typing import Dict, List, Optional

@task
async def typed_task(input_data: Dict[str, any]) -> Dict[str, List[int]]:
    """Well-typed task with clear input/output expectations."""
    return {"processed_numbers": [1, 2, 3]}

@workflow
async def typed_workflow(ctx: ExecutionContext[Dict[str, any]]) -> Dict[str, any]:
    """Type-safe workflow with clear data contracts."""
    result = await typed_task(ctx.input)
    return result
```

### 2. Handle Large Data Efficiently

```python
@workflow
async def efficient_large_data_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Best practices for handling large datasets."""

    # Use streaming for large inputs
    data_stream = ctx.input["data_stream_url"]

    # Process in chunks
    chunk_results = []
    async for chunk in stream_data(data_stream, chunk_size=1000):
        chunk_result = await process_chunk(chunk)
        chunk_results.append({"size": len(chunk), "checksum": hash(str(chunk))})

        # Force garbage collection for large chunks
        import gc
        gc.collect()

    # Return summary, not full data
    return {
        "chunks_processed": len(chunk_results),
        "total_items": sum(r["size"] for r in chunk_results),
        "execution_summary": "completed"
    }
```

### 3. Implement Robust Error Handling

```python
@task.with_options(retry_count=3, retry_delay=2)
async def resilient_data_task(data: dict) -> dict:
    """Task with robust error handling for data processing."""

    try:
        result = await process_with_validation(data)
        return {"success": True, "result": result}
    except ValidationError as e:
        # Return error information instead of failing
        return {"success": False, "error": "validation_failed", "details": str(e)}
    except Exception as e:
        # Log unexpected errors
        print(f"Unexpected error in data processing: {e}")
        raise

@workflow
async def robust_data_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow with comprehensive error handling."""

    results = []
    errors = []

    for item in ctx.input["items"]:
        result = await resilient_data_task(item)

        if result["success"]:
            results.append(result["result"])
        else:
            errors.append(result)

    return {
        "successful_items": len(results),
        "failed_items": len(errors),
        "errors": errors,
        "execution_id": ctx.execution_id
    }
```

### 4. Use Context for Metadata Tracking

```python
@workflow
async def metadata_aware_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that tracks comprehensive metadata."""

    start_time = await now()

    # Process data while tracking metadata
    result = await process_with_metadata_tracking(ctx.input, {
        "execution_id": ctx.execution_id,
        "workflow_name": ctx.workflow_name,
        "worker": ctx.current_worker,
        "start_time": start_time
    })

    end_time = await now()

    return {
        "result": result,
        "metadata": {
            "execution_id": ctx.execution_id,
            "processing_time": end_time - start_time,
            "events_count": len(ctx.events),
            "final_state": ctx.state.value
        }
    }
```

## Summary

This guide covered the essential aspects of data flow and state management in Flux:

- **Execution Context**: Central state container providing data access and workflow metadata
- **State Persistence**: Automatic checkpointing and recovery mechanisms for fault tolerance
- **Data Passing**: Patterns for efficient data flow between tasks
- **Memory Management**: Strategies for handling large datasets and optimizing memory usage
- **Advanced Patterns**: Pipeline processing, conditional flows, and robust error handling

Understanding these concepts enables you to build efficient, scalable workflows that handle data reliably and recover gracefully from failures.
