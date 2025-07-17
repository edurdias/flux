# Flux AI Coding Guidelines

## Architecture Overview

Flux is a distributed workflow orchestration engine with a **server-worker architecture**. The core components are:

- **Server** (`flux/server.py`): FastAPI-based HTTP server handling workflow execution coordination
- **Worker** (`flux/worker.py`): Distributed execution nodes that claim and execute workflows via SSE streams
- **ExecutionContext** (`flux/domain/execution_context.py`): Stateful workflow execution container with event tracking
- **Task/Workflow decorators** (`flux/task.py`, `flux/workflow.py`): Core programming model for defining executable units

## Key Patterns

### 1. Task & Workflow Definition
```python
@task.with_options(
    retry_max_attempts=3,
    timeout=30,
    fallback=fallback_func,
    rollback=rollback_func,
    cache=True
)
async def my_task(param: str) -> str:
    return f"processed {param}"

@workflow.with_options(requests=ResourceRequest(cpu=2, memory="4Gi"))
async def my_workflow(ctx: ExecutionContext[str]) -> str:
    return await my_task(ctx.input)
```

### 2. Execution Context Management
- **Context is thread-local**: Use `ExecutionContext.get()` to access current context in tasks
- **Event-driven state**: All task/workflow state changes emit `ExecutionEvent` objects
- **Checkpointing**: Context is automatically checkpointed via `ctx.checkpoint()` for pause/resume

### 3. Distributed Execution Pattern
- **Worker Registration**: Workers register capabilities (CPU, memory, packages) with server
- **Resource Matching**: Server matches workflows to workers based on `ResourceRequest` constraints
- **SSE Communication**: Workers receive execution instructions via Server-Sent Events
- **Base64 Encoding**: Workflow source code is base64-encoded for transport

### 4. Task Composition Patterns
```python
# Parallel execution
from flux.tasks import parallel
results = await parallel(task1(), task2(), task3())

# Pipeline processing
from flux.tasks import pipeline
result = await pipeline(task1, task2, task3, input=data)

# Task mapping
results = await my_task.map(input_list)
```

## Testing Conventions

### Example-Based Testing
Tests live in `tests/examples/` and test actual example workflows:
```python
def test_should_succeed():
    ctx = my_workflow.run("input")
    assert ctx.has_finished and ctx.has_succeeded
    return ctx
```

### State Assertions
Use ExecutionContext properties: `has_finished`, `has_succeeded`, `has_failed`, `is_paused`, `is_cancelled`

## Development Workflows

### Local Development
```bash
# Start server
flux start server

# Start worker
flux start worker

# Run workflow
flux workflow run my_workflow '{"input": "data"}'
```

### Configuration
- **Config file**: `flux.toml` (TOML format)
- **Environment-based**: Uses `pydantic-settings` for config overrides
- **Database**: SQLite by default (`flux.db`)

### CLI Commands
- `flux workflow list` - List registered workflows
- `flux workflow register file.py` - Register workflows from file
- `flux workflow run name payload` - Execute workflow
- `flux secrets set/get/list` - Manage secrets
- `flux start server/worker/mcp` - Start different server types

## Code Organization

### Domain Layer (`flux/domain/`)
- `ExecutionContext`: Core execution state container
- `events.py`: Event types and execution states
- `resource_request.py`: Worker resource matching logic

### Infrastructure Layer
- `context_managers.py`: Persistence layer (SQLite)
- `catalogs.py`: Workflow registry and versioning
- `worker_registry.py`: Worker capability tracking
- `secret_managers.py`: Secure credential management

### Task Execution
- **Error Handling**: Automatic retry → fallback → rollback chain
- **Timeout Management**: Per-task timeout with `asyncio.wait_for()`
- **Caching**: Optional task result caching via `CacheManager`
- **Secrets**: Injected via `secret_requests` parameter

## Important Constraints

1. **Python 3.12+ required** - Uses modern async/await patterns
2. **SQLAlchemy models** - All persistence through SQLAlchemy ORM
3. **Type hints mandatory** - Leverages generics for ExecutionContext typing
4. **Async-first design** - All task/workflow functions are async
5. **Poetry packaging** - Uses Poetry for dependency management and CLI entry points

## Common Pitfalls

- **Context Access**: Always use `ExecutionContext.get()` inside tasks, never pass manually
- **Worker Matching**: ResourceRequest must match actual worker capabilities
- **State Immutability**: Don't modify ExecutionContext state directly, use methods
- **Event Ordering**: Events are append-only and determine execution state
- **Base64 Encoding**: Workflow source must be base64-encoded for worker transport
