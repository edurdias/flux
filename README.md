# Flux

Flux is a distributed workflow orchestration engine written in Python that enables building stateful and fault-tolerant workflows. It provides an intuitive async programming model for creating complex, reliable distributed applications with built-in support for state management, error handling, and execution control.

**Current Version**: 0.2.7

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
      name="custom_task",             # Custom task name
      retry_max_attempts=3,           # Auto-retry failed tasks
      retry_delay=1,                  # Initial delay between retries
      retry_backoff=2,                # Exponential backoff for retries
      timeout=30,                     # Task execution timeout (seconds)
      fallback=fallback_func,         # Fallback handler for failures
      rollback=rollback_func,         # Rollback handler for cleanup
      secret_requests=['API_KEY'],    # Secure secrets management
      cache=True,                     # Enable task result caching
      metadata=True                   # Enable task metadata access
  )
  async def my_task():
      pass
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
from flux import task, workflow, ExecutionContext

@task
async def say_hello(name: str) -> str:
    return f"Hello, {name}"

@workflow
async def hello_world(ctx: ExecutionContext[str]):
    return await say_hello(ctx.input)

# Execute locally
result = hello_world.run("World")
print(result.output)  # "Hello, World"
```

### 2. Parallel Task Execution

Execute multiple tasks concurrently:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import parallel

@task
async def say_hi(name: str):
    return f"Hi, {name}"

@task
async def say_hello(name: str):
    return f"Hello, {name}"

@task
async def say_hola(name: str):
    return f"Hola, {name}"

@workflow
async def parallel_workflow(ctx: ExecutionContext[str]):
    results = await parallel(
        say_hi(ctx.input),
        say_hello(ctx.input),
        say_hola(ctx.input)
    )
    return results
```

### 3. Pipeline Processing

Chain tasks in a processing pipeline:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import pipeline

@task
async def multiply_by_two(x):
    return x * 2

@task
async def add_three(x):
    return x + 3

@task
async def square(x):
    return x * x

@workflow
async def pipeline_workflow(ctx: ExecutionContext[int]):
    result = await pipeline(
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
@task
async def process_item(item: str):
    return item.upper()

@workflow
async def map_workflow(ctx: ExecutionContext[list[str]]):
    results = await process_item.map(ctx.input)
    return results
```

## Advanced Usage

### Workflow Control
#### State Management
```python
# Resume existing workflow execution
ctx = workflow.run(execution_id="previous_execution_id")

# Check workflow state
print(f"Finished: {ctx.has_finished}")
print(f"Succeeded: {ctx.has_succeeded}")
print(f"Failed: {ctx.has_failed}")

# Inspect workflow events
for event in ctx.events:
    print(f"{event.type}: {event.value}")
```

### Error Handling

```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    fallback=lambda: "fallback result",
    rollback=cleanup_function
)
async def risky_task():
    # Task implementation with comprehensive error handling
    pass
```

### Secret Management

```python
@task.with_options(secret_requests=["API_KEY"])
async def secure_task(secrets: dict[str, Any] = {}):
    api_key = secrets["API_KEY"]
    # Use API key securely
```

Flux provides both a command-line interface and HTTP API endpoints for managing secrets:

#### Managing Secrets via CLI

```bash
# List all secrets (shows only names, not values)
flux secrets list

# Set a secret
flux secrets set API_KEY "your-api-key-value"

# Get a secret value (use cautiously)
flux secrets get API_KEY

# Remove a secret
flux secrets remove API_KEY
```

#### Managing Secrets via API

When running the Flux server, you can also manage secrets using the HTTP API:

```bash
# List all secrets (shows only names, not values)
curl -X GET 'http://localhost:8000/admin/secrets'

# Set or update a secret
curl -X POST 'http://localhost:8000/admin/secrets' \
     -H 'Content-Type: application/json' \
     -d '{"name": "API_KEY", "value": "your-api-key-value"}'

# Get a secret value
curl -X GET 'http://localhost:8000/admin/secrets/API_KEY'

# Delete a secret
curl -X DELETE 'http://localhost:8000/admin/secrets/API_KEY'
```

### Task Caching

Enable task result caching to avoid re-execution:

```python
@task.with_options(cache=True)
async def expensive_computation(input_data):
    # Results will be cached based on input
    return complex_calculation(input_data)
```

### Task Metadata

Access task metadata during execution:

```python
from flux.decorators import TaskMetadata

@task.with_options(metadata=True)
async def metadata_aware_task(data, metadata: TaskMetadata = {}):
    print(f"Task ID: {metadata.task_id}")
    print(f"Task Name: {metadata.task_name}")
    return process_data(data)
```

### Built-in Tasks

Flux provides several built-in tasks for common operations:

```python
from flux.tasks import now, sleep, uuid4, choice, randint, pause

@workflow
async def built_in_tasks_example(ctx: ExecutionContext):
    # Time operations
    start_time = await now()
    await sleep(2.5)  # Sleep for 2.5 seconds

    # Random operations
    random_choice = await choice(['option1', 'option2', 'option3'])
    random_number = await randint(1, 100)

    # UUID generation
    unique_id = await uuid4()

    # Workflow pause points
    await pause("wait_for_approval")

    return {
        'start_time': start_time,
        'choice': random_choice,
        'number': random_number,
        'id': str(unique_id)
    }
```

## Distributed Architecture

Flux supports distributed execution through a server and worker architecture:

### Start Server
Start the server to coordinate workflow execution:

```bash
flux start server
```

You can specify custom host and port:
```bash
flux start server --host 0.0.0.0 --port 8080
```

### Start Workers
Start worker nodes to execute tasks:

```bash
flux start worker
```

Workers automatically connect to the server and register themselves for task execution.

### Execute Workflows via HTTP
Once the server is running, you can execute workflows via HTTP. The API provides several endpoints for workflow management:

#### Upload and Register Workflows
```bash
# Upload a Python file containing workflows
curl -X POST 'http://localhost:8000/workflows' \
     -F 'file=@my_workflows.py'
```

#### List All Workflows
```bash
curl -X GET 'http://localhost:8000/workflows'
```

#### Get Workflow Details
```bash
curl -X GET 'http://localhost:8000/workflows/workflow_name'
```

#### Execute Workflows
Run workflows with different execution modes:

**Synchronous execution** (wait for completion):
```bash
curl -X POST 'http://localhost:8000/workflows/workflow_name/run/sync' \
     -H 'Content-Type: application/json' \
     -d '"input_data"'
```

**Asynchronous execution** (immediate response):
```bash
curl -X POST 'http://localhost:8000/workflows/workflow_name/run/async' \
     -H 'Content-Type: application/json' \
     -d '"input_data"'
```

**Streaming execution** (real-time updates):
```bash
curl -X POST 'http://localhost:8000/workflows/workflow_name/run/stream' \
     -H 'Content-Type: application/json' \
     -d '"input_data"'
```

#### Check Workflow Status
```bash
curl -X GET 'http://localhost:8000/workflows/workflow_name/status/execution_id'
```

For detailed execution information, add `?detailed=true`:
```bash
curl -X GET 'http://localhost:8000/workflows/workflow_name/status/execution_id?detailed=true'
```

#### API Documentation
The server provides interactive API documentation at:
- Swagger UI: `http://localhost:8000/docs`

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

The project uses several tools for code quality and development:

**Linting & Formatting:**
- **Ruff** - Fast Python linter and formatter (configured with 100-char line length)
- **Pylint** - Comprehensive code analysis
- **Pyflakes** - Fast Python source checker
- **Bandit** - Security vulnerability scanner
- **Prospector** - Meta-tool that runs multiple analysis tools

**Type Checking:**
- **Pyright** - Static type checker for Python

**Testing:**
- **Pytest** - Testing framework with coverage support
- **pytest-cov** - Coverage reporting
- **pytest-mock** - Mocking utilities

**Development Tools:**
- **Pre-commit** - Git hooks for automated code quality checks
- **Poethepoet** - Task runner for custom commands
- **Radon** - Code complexity analysis

**Documentation:**
- **MkDocs** with Material theme - Documentation generation
- **MkDocstrings** - Auto-generate API documentation

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

For more details, please check our [documentation](https://edurdias.github.io/flux/).
