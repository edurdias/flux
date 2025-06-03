# Parallel Processing

Learn how to speed up your workflows by executing tasks in parallel. This tutorial covers Flux's parallel processing capabilities and best practices for maximizing throughput.

## What You'll Learn

- How to use Flux's built-in parallel processing
- When to use parallel vs sequential processing
- Performance optimization techniques
- Handling errors in parallel execution
- Resource management and limits

## Prerequisites

- Completed [Your First Workflow](your-first-workflow.md)
- Understanding of [Working with Tasks](working-with-tasks.md)
- Flux server and worker running
- Basic understanding of concurrency concepts

## Understanding Parallel Processing

Parallel processing allows multiple tasks to run simultaneously, reducing total execution time for independent operations. Flux provides built-in support for parallel execution without requiring complex threading or multiprocessing code.

### When to Use Parallel Processing

✅ **Good candidates for parallel processing**:
- Independent data processing tasks
- Multiple API calls
- File processing operations
- Database queries that don't depend on each other
- Batch processing of similar items

❌ **Poor candidates for parallel processing**:
- Tasks that depend on previous results
- Resource-intensive operations (may overwhelm system)
- Tasks that modify shared state
- Operations with strict ordering requirements

## Step 1: Basic Parallel Execution

Let's start with a simple example. Create a file called `parallel_examples.py`:

```python
# parallel_examples.py
from flux import workflow, task, parallel
import time
import random
from datetime import datetime
from typing import List, Dict, Any

# Simulation tasks for demonstration
@task
def simulate_api_call(endpoint: str, delay: float = None) -> Dict[str, Any]:
    """Simulate an API call with variable delay."""
    if delay is None:
        delay = random.uniform(1, 3)  # Random delay 1-3 seconds

    print(f"Starting API call to {endpoint}...")
    time.sleep(delay)  # Simulate network delay

    result = {
        "endpoint": endpoint,
        "status": "success",
        "delay": delay,
        "timestamp": datetime.now().isoformat(),
        "data": f"Response from {endpoint}"
    }

    print(f"Completed API call to {endpoint} in {delay:.1f}s")
    return result

@task
def simulate_file_processing(filename: str, size: int = None) -> Dict[str, Any]:
    """Simulate file processing with variable processing time."""
    if size is None:
        size = random.randint(100, 1000)

    # Processing time proportional to file size
    processing_time = size / 1000  # 1 second per 1000 units

    print(f"Processing file {filename} (size: {size})...")
    time.sleep(processing_time)

    result = {
        "filename": filename,
        "size": size,
        "processing_time": processing_time,
        "lines_processed": size * 10,
        "timestamp": datetime.now().isoformat()
    }

    print(f"Completed processing {filename} in {processing_time:.1f}s")
    return result

# Sequential vs Parallel Comparison
@workflow
def sequential_processing() -> Dict[str, Any]:
    """Process tasks sequentially - one after another."""
    print("=== Sequential Processing ===")
    start_time = time.time()

    # Process each task one by one
    api_results = []
    endpoints = ["users", "orders", "products", "analytics"]

    for endpoint in endpoints:
        result = simulate_api_call(endpoint)
        api_results.append(result)

    total_time = time.time() - start_time

    return {
        "method": "sequential",
        "total_time": total_time,
        "results": api_results,
        "tasks_count": len(api_results)
    }

@workflow
def parallel_processing() -> Dict[str, Any]:
    """Process tasks in parallel - all at the same time."""
    print("=== Parallel Processing ===")
    start_time = time.time()

    # Process all tasks in parallel
    endpoints = ["users", "orders", "products", "analytics"]

    # Create list of task calls
    api_calls = [simulate_api_call(endpoint) for endpoint in endpoints]

    # Execute all tasks in parallel
    api_results = parallel(api_calls)

    total_time = time.time() - start_time

    return {
        "method": "parallel",
        "total_time": total_time,
        "results": api_results,
        "tasks_count": len(api_results)
    }
```

## Step 2: Real-World Parallel Processing Examples

Now let's create more practical examples:

```python
# Add to parallel_examples.py

@task
def fetch_user_data(user_id: int) -> Dict[str, Any]:
    """Fetch user data from multiple sources."""
    # Simulate fetching from different services
    profile_delay = random.uniform(0.5, 1.5)
    preferences_delay = random.uniform(0.3, 1.0)
    history_delay = random.uniform(1.0, 2.0)

    print(f"Fetching data for user {user_id}...")

    # Simulate total time as max of all delays (they happen in parallel in real API)
    total_delay = max(profile_delay, preferences_delay, history_delay)
    time.sleep(total_delay)

    return {
        "user_id": user_id,
        "profile": {"name": f"User {user_id}", "email": f"user{user_id}@example.com"},
        "preferences": {"theme": "dark", "notifications": True},
        "history": {"last_login": "2024-01-01", "session_count": random.randint(1, 100)},
        "fetch_time": total_delay
    }

@task
def process_batch_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single item from a batch."""
    item_id = item.get("id")
    complexity = item.get("complexity", 1)

    # Processing time based on complexity
    processing_time = complexity * random.uniform(0.1, 0.5)

    print(f"Processing item {item_id} (complexity: {complexity})...")
    time.sleep(processing_time)

    return {
        "id": item_id,
        "original_data": item,
        "processed": True,
        "processing_time": processing_time,
        "result_score": random.uniform(0.5, 1.0)
    }

@task
def validate_data_source(source: str) -> Dict[str, bool]:
    """Validate a data source."""
    print(f"Validating data source: {source}")

    # Simulate validation time
    validation_time = random.uniform(0.5, 2.0)
    time.sleep(validation_time)

    # Random validation results for demo
    is_available = random.choice([True, True, True, False])  # 75% success rate
    is_valid_format = random.choice([True, True, False])      # 67% success rate

    return {
        "source": source,
        "available": is_available,
        "valid_format": is_valid_format,
        "validation_time": validation_time
    }

# Complex Parallel Workflows
@workflow
def parallel_user_batch_processing(user_ids: List[int]) -> Dict[str, Any]:
    """Process multiple users in parallel."""
    print(f"Processing batch of {len(user_ids)} users in parallel")
    start_time = time.time()

    # Fetch all user data in parallel
    user_calls = [fetch_user_data(user_id) for user_id in user_ids]
    user_data = parallel(user_calls)

    # Process results
    total_time = time.time() - start_time
    successful_fetches = len([u for u in user_data if u is not None])

    return {
        "total_users": len(user_ids),
        "successful_fetches": successful_fetches,
        "total_time": total_time,
        "avg_time_per_user": total_time / len(user_ids) if user_ids else 0,
        "user_data": user_data
    }

@workflow
def parallel_batch_processing(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Process a batch of items in parallel."""
    print(f"Processing {len(items)} items in parallel")
    start_time = time.time()

    # Process all items in parallel
    processing_calls = [process_batch_item(item) for item in items]
    processed_items = parallel(processing_calls)

    # Calculate statistics
    total_time = time.time() - start_time
    total_processing_time = sum(item["processing_time"] for item in processed_items)
    avg_score = sum(item["result_score"] for item in processed_items) / len(processed_items)

    return {
        "total_items": len(items),
        "wall_clock_time": total_time,
        "total_processing_time": total_processing_time,
        "time_saved": total_processing_time - total_time,
        "average_score": avg_score,
        "processed_items": processed_items
    }

@workflow
def parallel_data_validation(sources: List[str]) -> Dict[str, Any]:
    """Validate multiple data sources in parallel."""
    print(f"Validating {len(sources)} data sources in parallel")
    start_time = time.time()

    # Validate all sources in parallel
    validation_calls = [validate_data_source(source) for source in sources]
    validations = parallel(validation_calls)

    # Analyze results
    total_time = time.time() - start_time
    available_sources = [v for v in validations if v["available"]]
    valid_sources = [v for v in validations if v["available"] and v["valid_format"]]

    return {
        "total_sources": len(sources),
        "available_sources": len(available_sources),
        "valid_sources": len(valid_sources),
        "validation_time": total_time,
        "success_rate": len(valid_sources) / len(sources) * 100,
        "validations": validations
    }
```

## Step 3: Error Handling in Parallel Processing

Parallel processing requires special attention to error handling:

```python
# Add to parallel_examples.py

@task
def unreliable_task(task_id: str, failure_rate: float = 0.3) -> Dict[str, Any]:
    """A task that sometimes fails to demonstrate error handling."""
    print(f"Running unreliable task {task_id}")

    # Simulate processing time
    time.sleep(random.uniform(0.5, 1.5))

    # Randomly fail based on failure rate
    if random.random() < failure_rate:
        raise Exception(f"Task {task_id} failed randomly")

    return {
        "task_id": task_id,
        "status": "success",
        "result": f"Task {task_id} completed successfully"
    }

@task
def safe_unreliable_task(task_id: str, failure_rate: float = 0.3) -> Dict[str, Any]:
    """A safe wrapper around unreliable task."""
    try:
        return unreliable_task(task_id, failure_rate)
    except Exception as e:
        print(f"Task {task_id} failed: {e}")
        return {
            "task_id": task_id,
            "status": "failed",
            "error": str(e)
        }

@workflow
def parallel_with_error_handling(task_ids: List[str]) -> Dict[str, Any]:
    """Demonstrate error handling in parallel processing."""
    print(f"Running {len(task_ids)} tasks in parallel with error handling")
    start_time = time.time()

    # Use safe wrapper tasks
    task_calls = [safe_unreliable_task(task_id) for task_id in task_ids]
    results = parallel(task_calls)

    # Analyze results
    total_time = time.time() - start_time
    successful_tasks = [r for r in results if r["status"] == "success"]
    failed_tasks = [r for r in results if r["status"] == "failed"]

    return {
        "total_tasks": len(task_ids),
        "successful_tasks": len(successful_tasks),
        "failed_tasks": len(failed_tasks),
        "success_rate": len(successful_tasks) / len(task_ids) * 100,
        "execution_time": total_time,
        "results": results
    }

@workflow
def parallel_with_retry_logic(task_ids: List[str], max_retries: int = 2) -> Dict[str, Any]:
    """Demonstrate retry logic in parallel processing."""
    print(f"Running {len(task_ids)} tasks with retry logic")

    final_results = []

    for task_id in task_ids:
        for attempt in range(max_retries + 1):
            try:
                result = unreliable_task(task_id, failure_rate=0.5)
                result["attempts"] = attempt + 1
                final_results.append(result)
                break
            except Exception as e:
                if attempt == max_retries:
                    # Final failure
                    final_results.append({
                        "task_id": task_id,
                        "status": "failed",
                        "error": str(e),
                        "attempts": attempt + 1
                    })
                else:
                    print(f"Task {task_id} attempt {attempt + 1} failed, retrying...")

    successful_tasks = [r for r in final_results if r.get("status") != "failed"]

    return {
        "total_tasks": len(task_ids),
        "successful_tasks": len(successful_tasks),
        "final_results": final_results
    }
```

## Step 4: Performance Testing and Optimization

Create test workflows to measure performance improvements:

```python
# Add to parallel_examples.py

@workflow
def performance_comparison_test() -> Dict[str, Any]:
    """Compare sequential vs parallel processing performance."""
    print("=== Performance Comparison Test ===")

    # Test data
    test_endpoints = ["api1", "api2", "api3", "api4", "api5"]

    # Sequential processing
    print("\n--- Testing Sequential Processing ---")
    sequential_result = sequential_processing()

    # Wait a moment between tests
    time.sleep(1)

    # Parallel processing
    print("\n--- Testing Parallel Processing ---")
    parallel_result = parallel_processing()

    # Calculate improvement
    time_saved = sequential_result["total_time"] - parallel_result["total_time"]
    improvement_percent = (time_saved / sequential_result["total_time"]) * 100

    return {
        "sequential": {
            "time": sequential_result["total_time"],
            "tasks": sequential_result["tasks_count"]
        },
        "parallel": {
            "time": parallel_result["total_time"],
            "tasks": parallel_result["tasks_count"]
        },
        "improvement": {
            "time_saved": time_saved,
            "improvement_percent": improvement_percent,
            "speedup_factor": sequential_result["total_time"] / parallel_result["total_time"]
        }
    }

@workflow
def scalability_test(batch_sizes: List[int] = [5, 10, 20, 50]) -> Dict[str, Any]:
    """Test how parallel processing scales with different batch sizes."""
    print("=== Scalability Test ===")

    results = []

    for batch_size in batch_sizes:
        print(f"\n--- Testing batch size: {batch_size} ---")

        # Create test data
        test_items = [
            {"id": i, "complexity": random.randint(1, 3)}
            for i in range(batch_size)
        ]

        # Test parallel processing
        start_time = time.time()
        result = parallel_batch_processing(test_items)

        batch_result = {
            "batch_size": batch_size,
            "wall_clock_time": result["wall_clock_time"],
            "total_processing_time": result["total_processing_time"],
            "efficiency": (result["total_processing_time"] / result["wall_clock_time"]),
            "throughput": batch_size / result["wall_clock_time"]  # items per second
        }

        results.append(batch_result)

        print(f"Batch {batch_size}: {batch_result['wall_clock_time']:.2f}s wall clock, "
              f"{batch_result['efficiency']:.1f}x efficiency")

    return {
        "test_results": results,
        "optimal_batch_size": max(results, key=lambda x: x["throughput"])["batch_size"]
    }
```

## Step 5: Register and Test Your Parallel Workflows

Register your workflows:

```bash
flux workflow register parallel_examples.py
```

Test the different parallel processing patterns:

### Basic Comparison
```bash
# Compare sequential vs parallel
flux workflow run performance_comparison_test
```

### Batch Processing
```bash
# Test parallel user processing
flux workflow run parallel_user_batch_processing --input '{"user_ids": [1, 2, 3, 4, 5]}'

# Test parallel item processing
flux workflow run parallel_batch_processing --input '{"items": [{"id": 1, "complexity": 2}, {"id": 2, "complexity": 1}, {"id": 3, "complexity": 3}]}'
```

### Error Handling
```bash
# Test error handling
flux workflow run parallel_with_error_handling --input '{"task_ids": ["task1", "task2", "task3", "task4", "task5"]}'
```

### Scalability Testing
```bash
# Test scalability
flux workflow run scalability_test
```

## Best Practices for Parallel Processing

### ✅ Do's

1. **Use for independent tasks**: Parallel processing works best when tasks don't depend on each other
2. **Batch appropriately**: Find the right balance between parallelism and resource usage
3. **Handle errors gracefully**: Always plan for task failures in parallel execution
4. **Monitor resource usage**: Watch CPU, memory, and network utilization
5. **Test performance**: Measure actual improvements, don't assume parallel is always faster

### ❌ Don'ts

1. **Don't overparallelized**: Too many parallel tasks can overwhelm the system
2. **Don't ignore dependencies**: Ensure tasks are truly independent
3. **Don't neglect error handling**: One failed task shouldn't break the entire workflow
4. **Don't assume linear speedup**: Real-world performance gains depend on many factors
5. **Don't forget about shared resources**: Database connections, API rate limits, etc.

## Performance Optimization Tips

### 1. Choose Optimal Batch Sizes

```python
@workflow
def optimized_batch_processing(items: List[Dict], max_parallel: int = 10):
    """Process items with optimal batch sizing."""

    # Process in chunks to avoid overwhelming the system
    results = []

    for i in range(0, len(items), max_parallel):
        batch = items[i:i + max_parallel]
        batch_calls = [process_batch_item(item) for item in batch]
        batch_results = parallel(batch_calls)
        results.extend(batch_results)

        print(f"Completed batch {i//max_parallel + 1}, "
              f"processed {len(batch_results)} items")

    return {"total_processed": len(results), "results": results}
```

### 2. Resource-Aware Processing

```python
@workflow
def resource_aware_processing(tasks: List[str], cpu_intensive: bool = False):
    """Adjust parallelism based on task characteristics."""

    if cpu_intensive:
        # Limit parallelism for CPU-intensive tasks
        max_parallel = 2
    else:
        # Higher parallelism for I/O-bound tasks
        max_parallel = 10

    # Process in resource-appropriate batches
    results = []
    for i in range(0, len(tasks), max_parallel):
        batch = tasks[i:i + max_parallel]
        if cpu_intensive:
            batch_results = parallel([cpu_intensive_task(task) for task in batch])
        else:
            batch_results = parallel([io_bound_task(task) for task in batch])
        results.extend(batch_results)

    return results
```

### 3. Adaptive Error Handling

```python
@workflow
def adaptive_parallel_processing(tasks: List[str], failure_threshold: float = 0.1):
    """Adapt processing strategy based on failure rate."""

    results = []
    failures = 0

    # Process initial batch to assess failure rate
    initial_batch_size = min(5, len(tasks))
    initial_batch = tasks[:initial_batch_size]

    initial_calls = [safe_unreliable_task(task) for task in initial_batch]
    initial_results = parallel(initial_calls)

    # Calculate initial failure rate
    initial_failures = len([r for r in initial_results if r["status"] == "failed"])
    failure_rate = initial_failures / len(initial_results)

    results.extend(initial_results)
    remaining_tasks = tasks[initial_batch_size:]

    if failure_rate > failure_threshold:
        print(f"High failure rate ({failure_rate:.1%}), switching to sequential processing")
        # Process remaining tasks sequentially for better error handling
        for task in remaining_tasks:
            result = safe_unreliable_task(task)
            results.append(result)
    else:
        print(f"Low failure rate ({failure_rate:.1%}), continuing with parallel processing")
        # Continue with parallel processing
        remaining_calls = [safe_unreliable_task(task) for task in remaining_tasks]
        remaining_results = parallel(remaining_calls)
        results.extend(remaining_results)

    total_failures = len([r for r in results if r["status"] == "failed"])

    return {
        "total_tasks": len(tasks),
        "successful_tasks": len(results) - total_failures,
        "failure_rate": total_failures / len(results),
        "processing_strategy": "adaptive",
        "results": results
    }
```

## What You've Learned

✅ **Parallel Execution**: How to use `parallel()` for concurrent task execution
✅ **Performance Benefits**: When and how parallel processing improves performance
✅ **Error Handling**: Managing failures in parallel workflows
✅ **Resource Management**: Optimizing batch sizes and resource usage
✅ **Best Practices**: Do's and don'ts for parallel processing
✅ **Performance Testing**: Measuring and optimizing parallel workflows

## What's Next?

Now that you understand parallel processing, explore these advanced topics:

1. **[Workflow Patterns](workflow-patterns.md)** - Learn advanced workflow composition patterns
2. **[State Management](state-management.md)** - Manage workflow state and persistence
3. **[Production Deployment](production-deployment.md)** - Deploy parallel workflows to production

## Troubleshooting Parallel Processing

### Common Issues

**Problem**: Parallel processing is slower than sequential
**Solution**: Check if tasks are I/O bound vs CPU bound, reduce batch size, or verify worker capacity

**Problem**: Out of memory errors with parallel processing
**Solution**: Reduce batch sizes, process in chunks, or optimize task memory usage

**Problem**: Some parallel tasks fail while others succeed
**Solution**: Implement proper error handling and consider retry logic

For more help, see the [Troubleshooting Guide](troubleshooting.md) or [FAQ](faq.md).

Great work! You now understand how to leverage parallel processing to build fast, efficient workflows! ⚡
