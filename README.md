# Flux

Flux is a distributed workflow orchestration engine written in Python that enables building stateful and fault-tolerant workflows. It provides an intuitive async programming model for creating complex, reliable distributed applications with built-in support for state management, error handling, and execution control.

**Current Version**: 0.6.0

## Key Features

### Core Capabilities
- **Stateful Execution**: Full persistence of workflow state and execution history
- **Distributed Architecture**: Support for both local and distributed execution modes
- **High Performance**: Efficient parallel task execution and workflow processing
- **Type Safety**: Leverages Python type hints for safer workflow development
- **API Integration**: Built-in FastAPI server for HTTP-based workflow execution
- **Model Context Protocol (MCP) Support**: Integration with AI development workflows through MCP server capabilities
- **Workflow Cancellation**: Cancel running workflows with both sync and async modes
- **Resource Monitoring**: Worker system with CPU, memory, and resource tracking
- **Configuration Management**: Flexible configuration system with environment-based settings

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
- **Task Mapping**: Apply tasks across collections of inputs with automatic parallel execution
- **Graph-based Workflows**: Define workflows as directed acyclic graphs (DAGs) with conditional execution
- **Dynamic Workflows**: Modify workflow behavior based on runtime conditions
- **Workflow Cancellation**: Cancel running workflows gracefully with proper cleanup
- **Pause and Resume**: Create workflow pause points with input data for human approval or external triggers

### Scheduling
- **Auto-scheduling**: Workflows automatically create schedules when registered
- **Cron Scheduling**: Full cron expression support with timezone awareness
- **Interval Scheduling**: Execute workflows at regular intervals (minutes, hours, days)
- **One-time Scheduling**: Schedule workflows to run once at a specific time
- **Schedule Management**: Pause, resume, modify, and delete schedules via CLI or API
- **Integrated Scheduler**: Built-in scheduler daemon runs within the server process
- **Execution History**: Track scheduled executions with detailed history and failure tracking

### Error Handling & Recovery
- **Automatic Retries**: Configurable retry policies with exponential backoff
- **Fallback Mechanisms**: Define alternative execution paths for failed tasks
- **Rollback Support**: Clean up resources and state after failures
- **Exception Handling**: Comprehensive error management with detailed logging
- **Timeout Management**: Prevent hung tasks and workflows with configurable timeouts
- **Workflow Cancellation**: Graceful cancellation of running workflows with proper state management

### State Management
- **Execution Persistence**: Durable storage of workflow state using SQLite
- **Pause & Resume**: Control workflow execution flow with input data support
- **Deterministic Replay**: Automatic replay of workflow events to maintain consistency
- **State Inspection**: Monitor workflow progress and state through comprehensive APIs
- **Execution Context**: Rich context management with event tracking and metadata
- **Checkpoint Support**: Automatic checkpointing for reliable state recovery

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

### 5. Scheduled Workflows

Define workflows that run automatically on a schedule:

```python
from flux import workflow, task, cron, interval, ExecutionContext

@task
async def generate_daily_report(date: str):
    return f"Report generated for {date}"

# Workflow with cron schedule - runs weekdays at 9 AM UTC
@workflow.with_options(
    name="daily_report",
    schedule=cron("0 9 * * MON-FRI", timezone="UTC")
)
async def daily_report_workflow(ctx: ExecutionContext):
    result = await generate_daily_report("today")
    return {"report": result}

# Workflow with interval schedule - runs every 6 hours
@workflow.with_options(
    name="data_sync",
    schedule=interval(hours=6, timezone="UTC")
)
async def data_sync_workflow(ctx: ExecutionContext):
    # Sync data logic
    return {"status": "synced"}
```

When you register these workflows, schedules are created automatically:

```bash
# Register workflow - schedule is auto-created
flux workflow register my_workflows.py

# View schedules
flux schedule list

# The auto-created schedules will be named:
# - daily_report_auto
# - data_sync_auto
```

## CLI Commands

Flux provides a comprehensive command-line interface for workflow management:

### Workflow Management
```bash
# List all registered workflows
flux workflow list
flux workflow list --format json

# Register workflows from a file
flux workflow register my_workflows.py

# Show workflow details
flux workflow show my_workflow

# Run a workflow
flux workflow run my_workflow '{"input": "data"}'
flux workflow run my_workflow '{"input": "data"}' --mode sync
flux workflow run my_workflow '{"input": "data"}' --mode stream

# Resume a paused workflow
flux workflow resume my_workflow execution_id '{"resume_data": "value"}'

# Check workflow execution status
flux workflow status my_workflow execution_id
flux workflow status my_workflow execution_id --detailed
```

### Server & Worker Management
```bash
# Start the Flux server (includes integrated scheduler)
flux start server
flux start server --host 0.0.0.0 --port 8080

# Start a worker
flux start worker
flux start worker worker-name --server-url http://server:8000

# Start MCP server
flux start mcp
flux start mcp --host localhost --port 8080 --transport sse
```

### Schedule Management
```bash
# List all schedules
flux schedule list
flux schedule list --format json

# Show schedule details
flux schedule show <schedule-id>

# Create a cron schedule
flux schedule create my_workflow daily_report \
  --cron "0 9 * * MON-FRI" \
  --timezone "UTC" \
  --description "Daily business report"

# Create an interval schedule
flux schedule create my_workflow hourly_sync \
  --interval-hours 1 \
  --description "Hourly data sync"

# Pause/resume schedules
flux schedule pause <schedule-id>
flux schedule resume <schedule-id>

# View execution history
flux schedule history <schedule-id> --limit 20

# Delete a schedule
flux schedule delete <schedule-id>
```

### Secret Management
```bash
# List all secrets (names only)
flux secrets list

# Set a secret
flux secrets set API_KEY "your-secret-value"

# Get a secret (with confirmation prompt)
flux secrets get API_KEY

# Remove a secret
flux secrets remove API_KEY
```

## HTTP API Endpoints

Flux provides a comprehensive REST API for workflow orchestration:

### Workflow Endpoints
```bash
# List all workflows
GET /workflows

# Register workflows from uploaded file
POST /workflows
Content-Type: multipart/form-data

# Get workflow details
GET /workflows/{workflow_name}

# Execute workflow
POST /workflows/{workflow_name}/run/{mode}
# mode: sync, async, stream
Content-Type: application/json

# Resume workflow execution
POST /workflows/{workflow_name}/resume/{execution_id}/{mode}
Content-Type: application/json

# Check execution status
GET /workflows/{workflow_name}/status/{execution_id}?detailed=false

# Cancel workflow execution
GET /workflows/{workflow_name}/cancel/{execution_id}?mode=async
```

### Worker Management Endpoints
```bash
# Register a worker
POST /workers/register
Authorization: Bearer {bootstrap_token}

# Worker connection (SSE stream)
GET /workers/{name}/connect
Authorization: Bearer {session_token}

# Claim execution
POST /workers/{name}/claim/{execution_id}
Authorization: Bearer {session_token}

# Send execution checkpoint
POST /workers/{name}/checkpoint/{execution_id}
Authorization: Bearer {session_token}
```

### Schedule Endpoints
```bash
# List all schedules
GET /schedules?workflow_id={workflow_id}&active_only=true

# Get schedule details
GET /schedules/{schedule_id}

# Create a schedule
POST /schedules
Content-Type: application/json
{
  "workflow_name": "my_workflow",
  "name": "schedule_name",
  "schedule_config": {
    "type": "cron",
    "cron_expression": "0 9 * * MON-FRI",
    "timezone": "UTC"
  },
  "description": "Schedule description"
}

# Pause a schedule
POST /schedules/{schedule_id}/pause

# Resume a schedule
POST /schedules/{schedule_id}/resume

# Delete a schedule
DELETE /schedules/{schedule_id}

# Get schedule execution history
GET /schedules/{schedule_id}/history?limit=50
```

### Admin Endpoints
```bash
# List all secrets
GET /admin/secrets

# Get secret value
GET /admin/secrets/{name}

# Create or update secret
POST /admin/secrets
Content-Type: application/json

# Delete secret
DELETE /admin/secrets/{name}
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
print(f"Cancelled: {ctx.is_cancelled}")
print(f"Paused: {ctx.is_paused}")

# Inspect workflow events
for event in ctx.events:
    print(f"{event.type}: {event.value}")
```

#### Workflow Cancellation
```python
# Workflows support cancellation through asyncio.CancelledError
@workflow
async def cancellable_workflow(ctx: ExecutionContext):
    try:
        await long_running_task()
        return "completed"
    except asyncio.CancelledError:
        # Cleanup resources
        await cleanup()
        raise  # Re-raise to mark as cancelled
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
from flux.tasks import now, sleep, uuid4, choice, randint, pause, parallel, pipeline, Graph

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

### Graph-based Task Composition

Create complex task dependencies using directed acyclic graphs:

```python
from flux.tasks import Graph

@task
async def get_name(input: str) -> str:
    return input

@task
async def say_hello(name: str) -> str:
    return f"Hello, {name}"

@workflow
async def graph_workflow(ctx: ExecutionContext[str]):
    hello = (
        Graph("hello_world")
        .add_node("get_name", get_name)
        .add_node("say_hello", say_hello)
        .add_edge("get_name", "say_hello")
        .start_with("get_name")
        .end_with("say_hello")
    )
    return await hello(ctx.input)
```

Graph features:
- Complex task dependencies with conditional execution
- Automatic validation of graph structure
- Built-in cycle detection
- Flexible error handling paths

## Model Context Protocol (MCP) Support

Flux includes a built-in MCP server for integration with AI development workflows:

### Starting the MCP Server

```bash
# Start MCP server (default: localhost:8080)
flux start mcp

# Custom host and port
flux start mcp --host 0.0.0.0 --port 8081
```

### MCP Tools Available

The MCP server provides comprehensive workflow orchestration tools:

**Workflow Management:**
- `list_workflows` - List all available workflows
- `get_workflow_details` - Get detailed workflow information
- `upload_workflow` - Upload and register new workflow files

**Workflow Execution:**
- `execute_workflow_sync` - Execute and wait for completion
- `execute_workflow_async` - Execute with immediate response
- `get_execution_status` - Check workflow execution status

**Workflow Control:**
- `cancel_execution` - Cancel running workflows
- `resume_workflow_sync` - Resume paused workflows (synchronous)
- `resume_workflow_async` - Resume paused workflows (asynchronous)

### Configuration

Configure MCP server settings in your configuration file:

```toml
[mcp]
name = "flux-workflows"
host = "localhost"
port = 8080
server_url = "http://localhost:8000"
transport = "sse"
```

## Configuration System

Flux provides a flexible configuration system that supports multiple sources and environment variables:

### Configuration Sources

Configuration is loaded in order of precedence:
1. Environment variables (highest precedence)
2. `flux.toml` file
3. `pyproject.toml` file (under `[tool.flux]`)
4. Default values (lowest precedence)

### Configuration File Example

Create a `flux.toml` file in your project root:

```toml
[flux]
debug = false
log_level = "INFO"
server_host = "localhost"
server_port = 8000
home = ".flux"
database_url = "sqlite:///.flux/flux.db"
serializer = "pkl"

[flux.workers]
bootstrap_token = "your-bootstrap-token"
server_url = "http://localhost:8000"
default_timeout = 300
retry_attempts = 3
retry_delay = 1
retry_backoff = 2

[flux.security]
encryption_key = "your-encryption-key"

[flux.mcp]
name = "flux-workflows"
host = "localhost"
port = 8080
server_url = "http://localhost:8000"
transport = "sse"

[flux.scheduling]
auto_schedule_enabled = true
auto_schedule_suffix = "_auto"
poll_interval = 30.0
max_concurrent_executions = 10
```

### Environment Variables

Override any configuration using environment variables with `FLUX_` prefix:

```bash
# Basic settings
export FLUX_DEBUG=true
export FLUX_LOG_LEVEL=DEBUG
export FLUX_SERVER_HOST=0.0.0.0
export FLUX_SERVER_PORT=9000

# Worker settings
export FLUX_WORKERS__BOOTSTRAP_TOKEN=secure-token
export FLUX_WORKERS__SERVER_URL=http://production:8000
export FLUX_WORKERS__DEFAULT_TIMEOUT=600

# Security settings
export FLUX_SECURITY__ENCRYPTION_KEY=your-secret-key

# MCP settings
export FLUX_MCP__HOST=0.0.0.0
export FLUX_MCP__PORT=8080
export FLUX_MCP__TRANSPORT=streamable-http

# Scheduling settings
export FLUX_SCHEDULING__AUTO_SCHEDULE_ENABLED=true
export FLUX_SCHEDULING__AUTO_SCHEDULE_SUFFIX=_auto
export FLUX_SCHEDULING__POLL_INTERVAL=30.0
export FLUX_SCHEDULING__MAX_CONCURRENT_EXECUTIONS=10
```

### Programmatic Configuration

Access and override configuration programmatically:

```python
from flux.config import Configuration

# Get current configuration
config = Configuration.get()
print(f"Server port: {config.settings.server_port}")
print(f"Worker bootstrap token: {config.settings.workers.bootstrap_token}")

# Override configuration for testing
config.override(
    debug=True,
    server_port=9000,
    workers={"bootstrap_token": "test-token"}
)

# Reload configuration from sources
config.reload()

# Reset to defaults
config.reset()
```

### Configuration Options

**Core Settings:**
- `debug` - Enable debug mode (default: False)
- `log_level` - Logging level (default: "INFO")
- `server_host` - Server bind host (default: "localhost")
- `server_port` - Server bind port (default: 8000)
- `database_url` - Database connection URL (default: "sqlite:///.flux/flux.db")
- `serializer` - Default serializer: "json" or "pkl" (default: "pkl")

**Worker Settings:**
- `bootstrap_token` - Token for worker registration
- `server_url` - Default server URL to connect to
- `default_timeout` - Default task timeout in seconds
- `retry_attempts` - Default retry attempts
- `retry_delay` - Default retry delay in seconds
- `retry_backoff` - Default retry backoff multiplier

**Security Settings:**
- `encryption_key` - Key for encrypting sensitive data

**MCP Settings:**
- `name` - MCP server name
- `host` - MCP server host
- `port` - MCP server port
- `transport` - Transport protocol: "stdio", "streamable-http", "sse"

**Scheduling Settings:**
- `auto_schedule_enabled` - Enable automatic schedule creation (default: true)
- `auto_schedule_suffix` - Suffix for auto-created schedule names (default: "_auto")
- `poll_interval` - Scheduler poll interval in seconds (default: 30.0)
- `max_concurrent_executions` - Max concurrent scheduled executions (default: 10)
- `execution_timeout` - Max execution time for scheduled workflows (default: 3600.0)
- `schedule_check_tolerance` - Time tolerance for cron matching (default: 1.0)
- `once_schedule_tolerance` - Time tolerance for one-time schedules (default: 60.0)

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
# Start a worker with auto-generated name
flux start worker

# Start a worker with specific name
flux start worker my-worker-01

# Connect to specific server
flux start worker --server-url http://production-server:8000
```

### Worker Features

**Automatic Registration:**
- Workers automatically register with the server using bootstrap tokens
- Registration includes system information, Python packages, and resource availability

**Resource Monitoring:**
- CPU and memory usage tracking
- GPU information (if available)
- Automatic resource-based task assignment

**Authentication & Security:**
- Bootstrap token authentication for initial registration
- Session token authentication for ongoing communication
- Secure execution environment isolation

**Fault Tolerance:**
- Automatic reconnection on network failures
- Graceful handling of server restarts
- Task execution recovery and checkpointing

**Real-time Communication:**
- Server-Sent Events (SSE) for real-time execution coordination
- Streaming execution updates and cancellation support
- Efficient task distribution and load balancing

### Worker Architecture

Workers connect to the server via SSE streams and handle:

1. **Execution Scheduled Events** - New workflow executions to run
2. **Execution Resumed Events** - Paused workflows to resume
3. **Execution Cancelled Events** - Running workflows to cancel

Workers report back execution progress through checkpoint APIs, enabling:
- Real-time execution monitoring
- Pause/resume functionality
- Distributed execution state management

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

### Streaming Execution Features

The streaming execution mode provides real-time workflow monitoring through Server-Sent Events:

**Real-time Updates:**
- Live execution state changes
- Event-driven progress notifications
- Immediate cancellation support
- Streaming pause/resume notifications

**Event Types:**
- `workflow.execution.running` - Workflow started
- `workflow.execution.paused` - Workflow paused
- `workflow.execution.completed` - Workflow finished successfully
- `workflow.execution.failed` - Workflow failed
- `workflow.execution.cancelled` - Workflow cancelled

**Example Streaming Response:**
```
event: workflow.execution.running
data: {"execution_id": "abc123", "state": "RUNNING", "timestamp": "..."}

event: workflow.execution.completed
data: {"execution_id": "abc123", "state": "COMPLETED", "output": "result", "timestamp": "..."}
```

#### Resume Paused Workflows
```bash
# Resume with new input data
curl -X POST 'http://localhost:8000/workflows/workflow_name/resume/execution_id/async' \
     -H 'Content-Type: application/json' \
     -d '{"resume_data": "value"}'
```

#### Cancel Running Workflows
```bash
# Asynchronous cancellation
curl -X GET 'http://localhost:8000/workflows/workflow_name/cancel/execution_id?mode=async'

# Synchronous cancellation (wait for completion)
curl -X GET 'http://localhost:8000/workflows/workflow_name/cancel/execution_id?mode=sync'
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
