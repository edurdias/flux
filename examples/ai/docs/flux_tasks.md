# Task System

## Task Creation

Tasks in Flux are Python functions decorated with `@task`. They represent individual units of work that can be composed into workflows.

### Basic Task Creation
```python
from flux import task

@task
async def simple_task(data: str):
    return data.upper()
```

### Configured Task Creation
```python
@task.with_options(
    name="process_data",                 # Custom task name
    retry_max_attempts=3,                # Maximum retry attempts
    retry_delay=1,                       # Initial delay between retries
    retry_backoff=2,                     # Backoff multiplier
    timeout=30,                          # Task timeout in seconds
    fallback=fallback_function,          # Fallback function
    rollback=rollback_function,          # Rollback function
    secret_requests=["API_KEY"],         # Required secrets
    output_storage=custom_storage        # Custom output storage
)
async def complex_task(data: str, secrets: dict = {}):
    # Task implementation using secrets
    return process_data(data, secrets["API_KEY"])
```

## Task Options

### Retry Configuration
```python
@task.with_options(
    retry_max_attempts=3,    # Try up to 3 times
    retry_delay=1,          # Wait 1 second initially
    retry_backoff=2         # Double delay after each retry
)
def retrying_task():
    # Task will retry with delays: 1s, 2s, 4s
    pass
```

### Timeout Configuration
```python
@task.with_options(timeout=30)
def timed_task():
    # Task will fail if it exceeds 30 seconds
    pass
```

### Fallback Configuration
```python
def fallback_handler(input_data):
    # Handle task failure
    return "fallback result"

@task.with_options(fallback=fallback_handler)
def task_with_fallback(input_data):
    # If this fails, fallback_handler is called
    pass
```

### Rollback Configuration
```python
def rollback_handler(input_data):
    # Clean up after task failure
    pass

@task.with_options(rollback=rollback_handler)
def task_with_rollback(input_data):
    # If this fails, rollback_handler is called
    pass
```

## Task Composition

Flux provides several ways to compose tasks:

### Parallel Execution
```python
from flux.tasks import parallel

@workflow
async def parallel_workflow(ctx: WorkflowExecutionContext):
    results = await parallel(
        task1(),
        task2(),
        task3()
    )
    return results
```

### Pipeline Processing
```python
from flux.tasks import pipeline

@workflow
async def pipeline_workflow(ctx: WorkflowExecutionContext):
    result = await pipeline(
        task1,
        task2,
        task3,
        input=ctx.input
    )
    return result
```

### Task Mapping
```python
@task
async def process_item(item: str):
    return item.upper()

@workflow
async def mapping_workflow(ctx: WorkflowExecutionContext):
    items = ["a", "b", "c"]
    results = await process_item.map(items)
    return results
```

## Error Handling

Tasks support multiple error handling strategies that can be combined:

### Retry Mechanism
```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2
)
def retrying_task():
    if random.random() < 0.5:
        raise ValueError("Task failed")
    return "success"
```

### Fallback Strategy
```python
def fallback_handler():
    return "fallback result"

@task.with_options(
    fallback=fallback_handler,
    retry_max_attempts=3
)
def task_with_fallback():
    # Retries first, then fallback if all retries fail
    pass
```

### Rollback Operations
```python
def rollback_handler():
    # Clean up resources
    pass

@task.with_options(rollback=rollback_handler)
def task_with_rollback():
    # Rollback is called if task fails
    pass
```

### Timeout Handling
```python
@task.with_options(
    timeout=30,
    fallback=fallback_handler
)
def timed_task():
    # Fails if exceeds 30 seconds, then calls fallback
    pass
```

## Built-in Tasks

Flux provides several built-in tasks for common operations:

### Time Operations
```python
from flux.tasks import now, sleep

@workflow
async def timing_workflow(ctx: WorkflowExecutionContext):
    start_time = await now()           # Get current time
    await sleep(timedelta(seconds=5))   # Sleep for duration
    end_time = await now()
    return end_time - start_time
```

### Random Operations
```python
from flux.tasks import choice, randint, randrange

@workflow
async def random_workflow(ctx: WorkflowExecutionContext):
    chosen = await choice(["a", "b", "c"])    # Random choice
    number = await randint(1, 10)             # Random integer
    range_num = await randrange(0, 10, 2)     # Random range
```

### UUID Generation
```python
from flux.tasks import uuid4

@workflow
async def id_workflow(ctx: WorkflowExecutionContext):
    new_id = await uuid4()  # Generate UUID
```

### Workflow Pauses
```python
from flux.tasks import pause

@workflow
async def approval_workflow(ctx: WorkflowExecutionContext):
    # Process something
    result = await process_data()

    # Pause the workflow with a named pause point
    await pause("wait_for_approval")

    # This code will only execute after the workflow is resumed
    return f"Approved: {result}"
```

### Graph-based Task Composition

The Graph task allows you to create complex task dependencies using a directed acyclic graph (DAG):

```python
from flux.tasks import Graph

@task
def get_name(input: str) -> str:
    return input

@task
def say_hello(name: str) -> str:
    return f"Hello, {name}"

@workflow
async def graph_workflow(ctx: WorkflowExecutionContext[str]):
    # Create a graph named "hello_world"
    hello = (
        Graph("hello_world")
        # Add nodes (tasks)
        .add_node("get_name", get_name)
        .add_node("say_hello", say_hello)
        # Define edges (dependencies)
        .add_edge("get_name", "say_hello")
        # Define entry and exit points
        .start_with("get_name")
        .end_with("say_hello")
    )

    # Execute the graph
    return await hello(ctx.input)
```

Graph features:
- Create complex task dependencies
- Define conditional execution paths
- Validate graph structure
- Automatic task ordering

#### Graph Construction

```python
# Create a more complex graph with conditions
graph = (
    Graph("complex_workflow")
    # Add nodes
    .add_node("task1", task1_func)
    .add_node("task2", task2_func)
    .add_node("task3", task3_func)

    # Add edges with conditions
    .add_edge("task1", "task2", condition=lambda result: result > 0)
    .add_edge("task1", "task3", condition=lambda result: result <= 0)

    # Define workflow boundaries
    .start_with("task1")
    .end_with("task2")
    .end_with("task3")

    # Validate graph structure
    .validate()
)
```

#### Graph Properties
- Nodes can have custom actions
- Edges can have conditions
- Automatic cycle detection
- Guaranteed execution order
- Built-in validation

#### Error Handling in Graphs
```python
def check_condition(result):
    return isinstance(result, str)

graph = (
    Graph("error_handling")
    .add_node("task1", safe_task)
    .add_node("fallback", fallback_task)
    .add_edge("task1", "fallback", condition=lambda r: not check_condition(r))
    .start_with("task1")
    .end_with("fallback")
)
```
