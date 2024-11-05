# Flux

Flux is a distributed workflow orchestration engine written in Python that enables building stateful and fault-tolerant workflows. It provides an intuitive programming model for creating complex, reliable distributed applications with built-in support for state management, error handling, and execution control.

## Key Features

### Core Capabilities
- **Stateful Execution**: Full persistence of workflow state and execution history
- **Distributed Architecture**: Support for both local and distributed execution modes
- **High Performance**: Efficient parallel task execution and workflow processing
- **Type Safety**: Leverages Python type hints for safer workflow development
- **API Integration**: Built-in FastAPI server for HTTP-based workflow execution

### Task Management
- **Flexible Task Configuration**:
  ```python
  @task.with_options(
      retry_max_attemps=3,        # Auto-retry failed tasks
      retry_delay=1,              # Initial delay between retries
      retry_backoff=2,            # Exponential backoff for retries
      timeout=30,                 # Task execution timeout
      fallback=fallback_func,     # Fallback handler for failures
      rollback=rollback_func,     # Rollback handler for cleanup
      secret_requests=['API_KEY'] # Secure secrets management
  )
  ```

### Workflow Patterns
- **Task Parallelization**: Execute multiple tasks concurrently
- **Pipeline Processing**: Chain tasks in sequential processing pipelines
- **Subworkflows**: Compose complex workflows from simpler ones
- **Task Mapping**: Apply tasks across collections of inputs
- **Graph-based Workflows**: Define workflows as directed acyclic graphs (DAGs)
- **Dynamic Workflows**: Modify workflow behavior based on runtime conditions

### Error Handling & Recovery
- **Automatic Retries**: Configurable retry policies with backoff
- **Fallback Mechanisms**: Define alternative execution paths
- **Rollback Support**: Clean up after failures
- **Exception Handling**: Comprehensive error management
- **Timeout Management**: Prevent hung tasks and workflows

### State Management
- **Execution Persistence**: Durable storage of workflow state
- **Pause & Resume**: Control workflow execution flow
- **Deterministic Replay**: Automatic replay of workflow events to maintain consistency
- **State Inspection**: Monitor workflow progress and state

## Installation

```bash
pip install flux-core
```

**Requirements**:
- Python 3.12 or later
- Dependencies are managed through Poetry

## Quick Start

### 1. Basic Workflow

Create a simple workflow that processes input:

```python
from flux import task, workflow, WorkflowExecutionContext

@task
def say_hello(name: str) -> str:
    return f"Hello, {name}"

@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    return (yield say_hello(ctx.input))

# Execute locally
result = hello_world.run("World")
print(result.output)  # "Hello, World"
```

### 2. Parallel Task Execution

Execute multiple tasks concurrently:

```python
from flux import task, workflow
from flux.tasks import parallel

@workflow
def parallel_workflow(ctx: WorkflowExecutionContext[str]):
    results = yield parallel(
        lambda: task1(ctx.input),
        lambda: task2(ctx.input),
        lambda: task3(ctx.input)
    )
    return results
```

### 3. Pipeline Processing

Chain tasks in a processing pipeline:

```python
from flux.tasks import pipeline

@workflow
def pipeline_workflow(ctx: WorkflowExecutionContext[int]):
    result = yield pipeline(
        multiply_by_two,
        add_three,
        square,
        input=ctx.input
    )
    return result
```

### 4. Task Mapping

Apply a task across multiple inputs:

```python
@workflow
def map_workflow(ctx: WorkflowExecutionContext[list[str]]):
    results = yield process_item.map(ctx.input)
    return results
```

## Advanced Usage

### Workflow Control

#### Pause and Resume
```python
@workflow
def pausable_workflow():
    yield pause("checkpoint_1")  # Pause execution
    yield next_task()           # Will resume from here

# Run until pause point
ctx = pausable_workflow.run()

# Resume execution
ctx = pausable_workflow.run(execution_id=ctx.execution_id)
```

#### State Management
```python
# Resume existing workflow execution
ctx = workflow.run(execution_id="previous_execution_id")

# Check workflow state
print(f"Finished: {ctx.finished}")
print(f"Succeeded: {ctx.succeeded}")
print(f"Failed: {ctx.failed}")
print(f"Paused: {ctx.paused}")

# Inspect workflow events
for event in ctx.events:
    print(f"{event.type}: {event.value}")
```

### Error Handling

```python
@task.with_options(
    retry_max_attemps=3,
    retry_delay=1,
    retry_backoff=2,
    fallback=lambda: "fallback result",
    rollback=cleanup_function
)
def risky_task():
    # Task implementation with comprehensive error handling
    pass
```

### Secret Management

```python
@task.with_options(secret_requests=["API_KEY"])
def secure_task(secrets: dict[str, Any] = {}):
    api_key = secrets["API_KEY"]
    # Use API key securely
```

## API Server

Start the API server for HTTP-based workflow execution:

```bash
flux start myworkflows
```

Execute workflows via HTTP:
```bash
curl -X POST 'http://localhost:8000/workflow_name' \
     -H 'Content-Type: application/json' \
     -d '"input_data"'
```

## Development

### Setup Development Environment
```bash
git clone https://github.com/edurdias/flux
cd flux
poetry install
```

### Run Tests
```bash
poetry run pytest
```

### Code Quality
The project uses several tools for code quality:
- Ruff for linting and formatting
- MyPy for type checking
- Pytest for testing
- Pre-commit hooks for code quality checks

## License

Apache License 2.0 - See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit pull requests. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request


## Documentation

For a more details, please check our [documentation](https://edurdias.github.io/flux/).
