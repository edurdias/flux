# Task Mapping and Iteration

Flux provides powerful task mapping and iteration capabilities that enable efficient processing of collections and dynamic task creation. These features are essential for building scalable data processing workflows and batch operations.

## Map Operations

### Basic Task Mapping

Apply a task to multiple items with automatic parallelization:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import map_task

@task
async def process_item(item: str):
    """Process a single item"""
    return item.upper()

@workflow
async def map_workflow(ctx: ExecutionContext[list]):
    # Map the task across all input items
    results = await map_task(process_item, ctx.input)
    return results

# Usage
items = ["hello", "world", "flux"]
result = map_workflow.run(items)
print(result.output)  # ["HELLO", "WORLD", "FLUX"]
```

### Parallel Mapping with Concurrency Control

Control the level of parallelism for resource management:

```python
from flux.tasks import map_task

@task
async def expensive_operation(data: dict):
    """CPU or I/O intensive operation"""
    # Simulate processing time
    await asyncio.sleep(1)
    return {"processed": data["value"] * 2}

@workflow
async def controlled_parallel_workflow(ctx: ExecutionContext[list]):
    # Limit concurrent execution to 5 tasks
    results = await map_task(
        expensive_operation,
        ctx.input,
        max_concurrency=5
    )
    return results
```

### Filtered Mapping

Apply tasks conditionally based on item properties:

```python
@task
async def conditional_processor(item: dict):
    """Process only items that meet criteria"""
    if item.get("priority") == "high":
        return await high_priority_processing(item)
    elif item.get("type") == "urgent":
        return await urgent_processing(item)
    else:
        return item  # Pass through unchanged

@workflow
async def filtered_workflow(ctx: ExecutionContext[list]):
    # Filter and process in one step
    filtered_items = [item for item in ctx.input if item.get("enabled", True)]
    results = await map_task(conditional_processor, filtered_items)
    return results
```

## Batch Processing

### Fixed-Size Batches

Process items in fixed-size batches to optimize resource usage:

```python
from flux.tasks import batch_task

@task
async def batch_processor(batch: list):
    """Process a batch of items together"""
    # Batch operations are often more efficient
    total = sum(item["value"] for item in batch)
    return {"batch_size": len(batch), "total": total}

@workflow
async def batch_workflow(ctx: ExecutionContext[list]):
    # Process in batches of 10
    results = await batch_task(
        batch_processor,
        ctx.input,
        batch_size=10
    )
    return results
```

### Dynamic Batch Sizing

Adjust batch sizes based on item properties or system conditions:

```python
@task
async def adaptive_batch_processor(items: list, system_load: float):
    """Adjust processing based on current system conditions"""
    if system_load > 0.8:
        # Smaller batches under high load
        batch_size = min(5, len(items))
    else:
        # Larger batches when system is idle
        batch_size = min(20, len(items))

    batches = [items[i:i+batch_size] for i in range(0, len(items), batch_size)]
    results = []

    for batch in batches:
        batch_result = await process_batch(batch)
        results.extend(batch_result)

    return results

@workflow
async def adaptive_workflow(ctx: ExecutionContext[list]):
    system_load = await get_system_load()
    return await adaptive_batch_processor(ctx.input, system_load)
```

### Stream Processing

Process items as they become available using async generators:

```python
@task
async def stream_processor(item_generator):
    """Process items from a stream"""
    results = []
    async for item in item_generator:
        processed = await process_single_item(item)
        results.append(processed)

        # Yield intermediate results for long streams
        if len(results) % 100 == 0:
            await pause(f"Processed {len(results)} items")

    return results

async def data_stream():
    """Async generator producing data items"""
    for i in range(1000):
        yield {"id": i, "data": f"item_{i}"}

@workflow
async def stream_workflow(ctx: ExecutionContext):
    stream = data_stream()
    return await stream_processor(stream)
```

## Dynamic Task Creation

### Runtime Task Generation

Create tasks dynamically based on runtime conditions:

```python
from flux.tasks import parallel

@workflow
async def dynamic_task_workflow(ctx: ExecutionContext[dict]):
    config = ctx.input
    tasks = []

    # Create tasks based on configuration
    for processor_config in config["processors"]:
        if processor_config["type"] == "image":
            tasks.append(image_processor(processor_config))
        elif processor_config["type"] == "text":
            tasks.append(text_processor(processor_config))
        elif processor_config["type"] == "video":
            tasks.append(video_processor(processor_config))

    # Execute all generated tasks in parallel
    results = await parallel(*tasks)
    return {"results": results, "task_count": len(tasks)}
```

### Recursive Task Creation

Build recursive workflows for hierarchical data processing:

```python
@task
async def process_tree_node(node: dict, depth: int = 0):
    """Recursively process tree structures"""
    result = {"value": node["value"], "depth": depth}

    if "children" in node:
        child_tasks = [
            process_tree_node(child, depth + 1)
            for child in node["children"]
        ]
        child_results = await parallel(*child_tasks)
        result["children"] = child_results

    return result

@workflow
async def tree_processing_workflow(ctx: ExecutionContext[dict]):
    return await process_tree_node(ctx.input)
```

### Task Factory Pattern

Use factory functions to create specialized task instances:

```python
def create_data_processor(processor_type: str, config: dict):
    """Factory function for creating specialized processors"""

    @task
    async def specialized_processor(data):
        if processor_type == "ml_model":
            return await ml_model_processing(data, config)
        elif processor_type == "etl":
            return await etl_processing(data, config)
        elif processor_type == "validation":
            return await validation_processing(data, config)
        else:
            raise ValueError(f"Unknown processor type: {processor_type}")

    return specialized_processor

@workflow
async def factory_workflow(ctx: ExecutionContext[dict]):
    processors = []

    for proc_config in ctx.input["processing_pipeline"]:
        processor = create_data_processor(
            proc_config["type"],
            proc_config["config"]
        )
        processors.append(processor)

    # Create pipeline of processors
    data = ctx.input["data"]
    for processor in processors:
        data = await processor(data)

    return data
```

## Advanced Iteration Patterns

### Map-Reduce Operations

Implement distributed map-reduce patterns:

```python
@task
async def mapper(chunk: list):
    """Map phase: process data chunks"""
    mapped_data = []
    for item in chunk:
        processed = {"key": item["category"], "value": item["amount"]}
        mapped_data.append(processed)
    return mapped_data

@task
async def reducer(grouped_data: dict):
    """Reduce phase: aggregate mapped results"""
    return {
        "category": grouped_data["key"],
        "total": sum(item["value"] for item in grouped_data["values"])
    }

@workflow
async def map_reduce_workflow(ctx: ExecutionContext[list]):
    # Map phase: process in parallel chunks
    chunk_size = 100
    chunks = [ctx.input[i:i+chunk_size] for i in range(0, len(ctx.input), chunk_size)]

    mapped_results = await parallel(*[mapper(chunk) for chunk in chunks])

    # Shuffle phase: group by key
    grouped = {}
    for chunk_result in mapped_results:
        for item in chunk_result:
            key = item["key"]
            if key not in grouped:
                grouped[key] = {"key": key, "values": []}
            grouped[key]["values"].append(item)

    # Reduce phase: aggregate by key
    reduced_results = await parallel(*[reducer(group) for group in grouped.values()])

    return reduced_results
```

### Pipeline Processing with Iteration

Combine iteration with pipeline patterns:

```python
from flux.tasks import pipeline

@task
async def extract_batch(batch_ids: list):
    """Extract data for a batch of IDs"""
    return [await extract_item(id) for id in batch_ids]

@task
async def transform_batch(batch_data: list):
    """Transform extracted data"""
    return [await transform_item(item) for item in batch_data]

@task
async def load_batch(batch_data: list):
    """Load transformed data"""
    return await bulk_load(batch_data)

@workflow
async def etl_pipeline_workflow(ctx: ExecutionContext[list]):
    # Process IDs in batches through ETL pipeline
    batch_size = 50
    batches = [ctx.input[i:i+batch_size] for i in range(0, len(ctx.input), batch_size)]

    # Process each batch through the pipeline
    pipeline_tasks = []
    for batch in batches:
        pipeline_task = pipeline(
            lambda: extract_batch(batch),
            transform_batch,
            load_batch
        )
        pipeline_tasks.append(pipeline_task)

    # Execute all pipelines in parallel
    results = await parallel(*pipeline_tasks)
    return {"batches_processed": len(results), "total_items": len(ctx.input)}
```

### Iterative Refinement

Implement iterative algorithms that improve results over multiple iterations:

```python
@task
async def refine_model(model_data: dict, iteration: int):
    """Refine model in each iteration"""
    # Apply refinement algorithm
    refined = await apply_refinement(model_data, iteration)

    # Check convergence criteria
    improvement = calculate_improvement(model_data, refined)

    return {
        "model": refined,
        "improvement": improvement,
        "iteration": iteration,
        "converged": improvement < 0.001
    }

@workflow
async def iterative_refinement_workflow(ctx: ExecutionContext[dict]):
    model = ctx.input["initial_model"]
    max_iterations = ctx.input.get("max_iterations", 10)

    for iteration in range(max_iterations):
        result = await refine_model(model, iteration)
        model = result["model"]

        # Stop if converged
        if result["converged"]:
            return {
                "final_model": model,
                "iterations": iteration + 1,
                "converged": True
            }

        # Pause between iterations for monitoring
        if iteration % 5 == 4:
            await pause(f"Completed {iteration + 1} iterations")

    return {
        "final_model": model,
        "iterations": max_iterations,
        "converged": False
    }
```

## Performance Optimization

### Memory-Efficient Processing

Handle large datasets without excessive memory usage:

```python
@task
async def memory_efficient_processor(data_source: str, chunk_size: int = 1000):
    """Process large datasets in memory-efficient chunks"""
    results = []
    processed_count = 0

    async for chunk in read_data_chunks(data_source, chunk_size):
        chunk_result = await process_chunk(chunk)
        results.extend(chunk_result)
        processed_count += len(chunk)

        # Periodic cleanup and progress reporting
        if processed_count % 10000 == 0:
            await pause(f"Processed {processed_count} items")
            # Force garbage collection if needed
            import gc
            gc.collect()

    return results
```

### Adaptive Concurrency

Dynamically adjust concurrency based on system performance:

```python
@task
async def adaptive_concurrent_processor(items: list):
    """Adjust concurrency based on system metrics"""
    initial_concurrency = 10
    max_concurrency = 50
    min_concurrency = 2

    current_concurrency = initial_concurrency
    processed = 0
    results = []

    while processed < len(items):
        # Get current batch
        batch_end = min(processed + current_concurrency, len(items))
        batch = items[processed:batch_end]

        # Measure processing time
        start_time = time.time()
        batch_results = await parallel(*[process_item(item) for item in batch])
        processing_time = time.time() - start_time

        results.extend(batch_results)
        processed += len(batch)

        # Adjust concurrency based on performance
        if processing_time < 1.0 and current_concurrency < max_concurrency:
            current_concurrency = min(current_concurrency + 2, max_concurrency)
        elif processing_time > 5.0 and current_concurrency > min_concurrency:
            current_concurrency = max(current_concurrency - 2, min_concurrency)

    return results
```

## Best Practices

### Resource Management

1. **Concurrency Limits**: Always set appropriate concurrency limits
2. **Memory Monitoring**: Monitor memory usage in long-running iterations
3. **Resource Cleanup**: Clean up resources between iterations

### Error Handling

1. **Partial Failure Recovery**: Handle failures in individual items gracefully
2. **Checkpoint Progress**: Use pause points to save intermediate progress
3. **Retry Strategies**: Implement retry logic for transient failures

### Performance

1. **Batch Sizing**: Optimize batch sizes for your workload
2. **Resource Utilization**: Monitor and adapt to system resource availability
3. **Progress Tracking**: Provide visibility into long-running iterations

### Design Patterns

1. **Stateless Tasks**: Keep individual tasks stateless for better parallelization
2. **Immutable Data**: Use immutable data structures to avoid race conditions
3. **Composable Operations**: Design tasks to be easily composable and reusable
