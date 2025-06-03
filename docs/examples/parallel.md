# Parallel Processing Examples

This section demonstrates various parallel processing patterns and concurrent execution strategies in Flux workflows.

## Basic Parallel Execution

The simplest form of parallel execution using the `parallel()` built-in task.

**File:** `examples/parallel_tasks.py`

```python
from flux import ExecutionContext
from flux.task import task
from flux.tasks import parallel
from flux.workflow import workflow

@task
async def say_hi(name: str):
    return f"Hi, {name}"

@task
async def say_hello(name: str):
    return f"Hello, {name}"

@task
async def diga_ola(name: str):
    return f"Ola, {name}"

@task
async def saluda(name: str):
    return f"Hola, {name}"

@workflow
async def parallel_tasks_workflow(ctx: ExecutionContext[str]):
    results = await parallel(
        say_hi(ctx.input),
        say_hello(ctx.input),
        diga_ola(ctx.input),
        saluda(ctx.input),
    )
    return results
```

**Key concepts demonstrated:**
- Concurrent task execution with `parallel()`
- Independent task processing
- Result aggregation from parallel tasks
- Performance improvement through parallelization

## Data Processing Pipeline with Parallel Steps

Combines sequential pipeline processing with parallel data processing.

**File:** `examples/complex_pipeline.py`

```python
from flux.tasks import parallel, pipeline
import pandas as pd

@task
async def split_data(df: pd.DataFrame) -> list[pd.DataFrame]:
    return np.array_split(df, 10)

@task
async def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop("email", axis=1)

@task
async def aggregate_data(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(dfs, ignore_index=True)

@workflow
async def parallel_data_processing(ctx: ExecutionContext[str]):
    return await pipeline(
        load_data(ctx.input),
        split_data,
        lambda chunks: parallel(*[clean_data(chunk) for chunk in chunks]),
        aggregate_data
    )
```

**Key concepts demonstrated:**
- Data partitioning for parallel processing
- Lambda functions for dynamic parallel task creation
- Result aggregation after parallel processing
- Scalable data processing patterns

## Map-Reduce Pattern

Demonstrates the classic map-reduce pattern using Flux parallel processing.

**Example pattern:**
```python
from flux.tasks import parallel

@task
async def map_function(item: dict) -> dict:
    # Process individual item
    return {"id": item["id"], "processed": True}

@task
async def reduce_function(results: list[dict]) -> dict:
    # Aggregate results
    return {
        "total_processed": len(results),
        "items": results
    }

@workflow
async def map_reduce_workflow(ctx: ExecutionContext[list[dict]]):
    # Map phase - process items in parallel
    mapped_results = await parallel(
        *[map_function(item) for item in ctx.input]
    )

    # Reduce phase - aggregate results
    return await reduce_function(mapped_results)
```

**Key concepts demonstrated:**
- Map-reduce computational pattern
- Dynamic parallel task creation
- Scalable data processing
- Result aggregation strategies

## Parallel API Requests

Demonstrates efficient parallel processing of multiple API requests.

```python
import aiohttp
from flux.tasks import parallel

@task
async def fetch_url(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return {
                "url": url,
                "status": response.status,
                "data": await response.json()
            }

@workflow
async def parallel_api_requests(ctx: ExecutionContext[list[str]]):
    # Process multiple URLs in parallel
    results = await parallel(
        *[fetch_url(url) for url in ctx.input]
    )
    return results
```

**Key concepts demonstrated:**
- Parallel I/O operations
- External API integration
- Concurrent HTTP requests
- Error handling in parallel operations

## Load Balancing and Resource Management

Advanced parallel processing with resource constraints and load balancing.

```python
import asyncio
from typing import Any

@task
async def cpu_intensive_task(data: Any, worker_id: int) -> dict:
    # Simulate CPU-intensive work
    await asyncio.sleep(2)  # Simulate processing time
    return {
        "worker_id": worker_id,
        "result": f"Processed by worker {worker_id}",
        "data": data
    }

@workflow
async def load_balanced_processing(ctx: ExecutionContext[list[Any]]):
    # Distribute work across available workers
    max_workers = 4

    # Create worker pools
    worker_tasks = []
    for i, item in enumerate(ctx.input):
        worker_id = i % max_workers
        worker_tasks.append(cpu_intensive_task(item, worker_id))

    # Execute with controlled parallelism
    results = await parallel(*worker_tasks)
    return results
```

**Key concepts demonstrated:**
- Resource constraint management
- Load balancing across workers
- Controlled parallelism
- Worker pool patterns

## Nested Parallel Operations

Complex workflows with multiple levels of parallel processing.

```python
@task
async def process_batch(batch: list[dict]) -> list[dict]:
    # Process a batch of items in parallel
    return await parallel(
        *[process_item(item) for item in batch]
    )

@task
async def process_item(item: dict) -> dict:
    # Process individual item
    return {"id": item["id"], "processed": True}

@workflow
async def nested_parallel_workflow(ctx: ExecutionContext[list[list[dict]]]):
    # Process multiple batches in parallel,
    # each batch processes its items in parallel
    batch_results = await parallel(
        *[process_batch(batch) for batch in ctx.input]
    )

    # Flatten results
    flattened = []
    for batch_result in batch_results:
        flattened.extend(batch_result)

    return flattened
```

**Key concepts demonstrated:**
- Multi-level parallel processing
- Nested parallelism patterns
- Complex result aggregation
- Hierarchical task organization

## Performance Optimization

Best practices for optimizing parallel processing performance.

### Batch Size Optimization
```python
@task
async def optimal_batch_processing(items: list[Any]) -> list[Any]:
    # Determine optimal batch size based on system resources
    import os
    cpu_count = os.cpu_count() or 4
    batch_size = max(1, len(items) // (cpu_count * 2))

    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

    return await parallel(
        *[process_batch(batch) for batch in batches]
    )
```

### Memory-Efficient Processing
```python
@task
async def memory_efficient_parallel(large_dataset: list[Any]) -> Any:
    # Process in chunks to avoid memory exhaustion
    chunk_size = 1000
    results = []

    for i in range(0, len(large_dataset), chunk_size):
        chunk = large_dataset[i:i + chunk_size]
        chunk_results = await parallel(
            *[process_item(item) for item in chunk]
        )
        results.extend(chunk_results)

    return results
```

## Running Parallel Examples

```bash
# Basic parallel execution
python examples/parallel_tasks.py

# Complex parallel data processing
python examples/complex_pipeline.py

# Custom parallel patterns
python -c "
from examples.parallel_examples import map_reduce_workflow
data = [{'id': i, 'value': i*2} for i in range(10)]
ctx = map_reduce_workflow.run(data)
print(ctx.to_json())
"
```

## Performance Considerations

1. **CPU vs I/O Bound**: Use parallel processing for I/O-bound tasks, consider CPU limits for CPU-bound tasks
2. **Memory Usage**: Monitor memory consumption with large parallel operations
3. **Resource Limits**: Respect system resource limits and external service rate limits
4. **Error Handling**: One failed task doesn't stop others, but handle partial failures
5. **Debugging**: Parallel execution can make debugging more complex - use comprehensive logging
6. **Testing**: Test with various load levels to find optimal parallelism settings

## Monitoring Parallel Workflows

```python
import time
from flux.context import ExecutionContext

@workflow
async def monitored_parallel_workflow(ctx: ExecutionContext[list[Any]]):
    start_time = time.time()

    results = await parallel(
        *[monitored_task(item) for item in ctx.input]
    )

    end_time = time.time()

    return {
        "results": results,
        "execution_time": end_time - start_time,
        "parallel_tasks": len(ctx.input),
        "efficiency": len(ctx.input) / (end_time - start_time)
    }
```

This monitoring approach helps optimize parallel processing performance and identify bottlenecks.
