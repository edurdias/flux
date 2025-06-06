# Local Execution

Flux provides multiple ways to execute workflows locally, making it ideal for development, testing, and small-scale automation tasks. Local execution runs workflows directly in your Python process without requiring a distributed server/worker architecture.

## Direct Python Execution

The simplest way to run Flux workflows is direct execution within your Python script.

### Basic Local Execution

```python
from flux import task, workflow, ExecutionContext

@task
async def process_data(data: str) -> str:
    return f"Processed: {data.upper()}"

@workflow
async def local_workflow(ctx: ExecutionContext[str]):
    result = await process_data(ctx.input)
    return result

# Execute locally
if __name__ == "__main__":
    result = local_workflow.run("hello world")
    print(f"Output: {result.output}")  # "Output: Processed: HELLO WORLD"
    print(f"Execution ID: {result.execution_id}")
    print(f"State: {result.state}")
```

### Working with Complex Data Types

Local execution seamlessly handles complex Python objects:

```python
from typing import Dict, List
from dataclasses import dataclass

@dataclass
class UserData:
    name: str
    email: str
    age: int

@task
async def validate_user(user: UserData) -> UserData:
    if user.age < 0:
        raise ValueError("Age cannot be negative")
    return user

@task
async def format_greeting(user: UserData) -> str:
    return f"Hello {user.name} ({user.email}), you are {user.age} years old"

@workflow
async def user_processing(ctx: ExecutionContext[UserData]):
    validated = await validate_user(ctx.input)
    greeting = await format_greeting(validated)
    return {"user": validated, "greeting": greeting}

# Execute with complex data
user = UserData(name="Alice", email="alice@example.com", age=30)
result = user_processing.run(user)
print(result.output["greeting"])
```

## Local CLI Execution

Use the Flux CLI to run workflows locally without starting the distributed server.

### Registering and Running Workflows

```bash
# Save your workflow to a file (e.g., my_workflow.py)
# Then register it with the local CLI
flux workflow register my_workflow.py

# List registered workflows
flux workflow list

# Run workflow locally
flux workflow run my_workflow '"input_data"'
```

### CLI Execution Examples

```bash
# String input
flux workflow run hello_world '"Alice"'

# JSON input for complex data
flux workflow run data_processor '{"name": "Alice", "value": 42}'

# Array input
flux workflow run batch_processor '[1, 2, 3, 4, 5]'

# Boolean input
flux workflow run feature_toggle 'true'

# Numeric input
flux workflow run calculator '42'
```

## Development Workflow

### Interactive Development

For rapid development and testing, use Python's interactive capabilities:

```python
# In IPython or Jupyter
from flux import task, workflow, ExecutionContext

@task
async def debug_task(data: str) -> str:
    print(f"Processing: {data}")  # Debug output
    return data.upper()

@workflow
async def debug_workflow(ctx: ExecutionContext[str]):
    result = await debug_task(ctx.input)
    return result

# Test immediately
result = debug_workflow.run("test")
print(result.output)
```

### Hot Reloading During Development

Flux automatically reloads workflow definitions when files change:

```python
# my_workflow.py
from flux import task, workflow, ExecutionContext

@task
async def process(data: str) -> str:
    # Modify this function and re-run without restarting
    return f"Version 2: {data}"

@workflow
async def dev_workflow(ctx: ExecutionContext[str]):
    return await process(ctx.input)

if __name__ == "__main__":
    # This will pick up changes automatically
    result = dev_workflow.run("test")
    print(result.output)
```

## Local State Persistence

Even in local execution, Flux persists workflow state for reliability.

### Understanding Local State Storage

```bash
# Flux creates local state directories
your-project/
├── .data/
│   ├── flux.db          # Execution state database
│   └── secrets.db       # Local secrets storage
├── my_workflow.py
└── logs/               # Optional: Application logs
```

### Inspecting Local Execution State

```python
from flux import task, workflow, ExecutionContext
from flux.context_managers import ContextManager

@workflow
async def stateful_workflow(ctx: ExecutionContext[str]):
    # Workflow execution is automatically persisted
    return f"Processed: {ctx.input}"

# Execute workflow
result = stateful_workflow.run("test data")

# Inspect execution state
manager = ContextManager.create()
execution_context = manager.get(result.execution_id)

print(f"Execution ID: {execution_context.execution_id}")
print(f"State: {execution_context.state}")
print(f"Events: {len(execution_context.events)}")
print(f"Output: {execution_context.output}")
```

## Performance Considerations

### Parallel Execution in Local Mode

Flux efficiently handles parallel tasks even in local execution:

```python
from flux.tasks import parallel

@task
async def cpu_intensive_task(data: int) -> int:
    # Simulate CPU-intensive work
    import time
    time.sleep(0.1)  # Don't use in real async code
    return data * 2

@workflow
async def parallel_local_workflow(ctx: ExecutionContext[List[int]]):
    # These tasks run in parallel using asyncio
    results = await parallel(
        cpu_intensive_task(ctx.input[0]),
        cpu_intensive_task(ctx.input[1]),
        cpu_intensive_task(ctx.input[2]),
        cpu_intensive_task(ctx.input[3])
    )
    return results

# Efficient parallel execution locally
result = parallel_local_workflow.run([1, 2, 3, 4])
print(result.output)  # [2, 4, 6, 8]
```

### Memory Management

Local execution is memory-efficient with automatic cleanup:

```python
@task
async def memory_intensive_task(size: int) -> str:
    # Large data processing
    large_data = [i for i in range(size)]
    result = f"Processed {len(large_data)} items"
    # Memory is automatically cleaned up after task completion
    return result

@workflow
async def memory_workflow(ctx: ExecutionContext[int]):
    return await memory_intensive_task(ctx.input)

# Memory is efficiently managed
result = memory_workflow.run(1000000)
```

## Error Handling in Local Mode

### Debugging Failed Executions

```python
from flux.errors import TaskExecutionError

@task
async def failing_task(data: str) -> str:
    if data == "error":
        raise ValueError("Intentional error for testing")
    return data.upper()

@workflow
async def error_handling_workflow(ctx: ExecutionContext[str]):
    try:
        return await failing_task(ctx.input)
    except ValueError as e:
        return f"Handled error: {str(e)}"

# Test error handling
result = error_handling_workflow.run("error")
print(result.output)  # "Handled error: Intentional error for testing"
```

### Accessing Execution Events

```python
@workflow
async def logged_workflow(ctx: ExecutionContext[str]):
    result = f"Processed: {ctx.input}"
    return result

# Execute and inspect events
result = logged_workflow.run("test")

# Access execution context for debugging
manager = ContextManager.create()
execution_context = manager.get(result.execution_id)

for event in execution_context.events:
    print(f"{event.time}: {event.type} - {event.data}")
```

## Integration with Development Tools

### VSCode Integration

Create a `.vscode/launch.json` for debugging workflows:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug Flux Workflow",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/my_workflow.py",
            "console": "integratedTerminal",
            "justMyCode": true,
            "env": {
                "FLUX_LOG_LEVEL": "DEBUG"
            }
        }
    ]
}
```

### Testing Integration

```python
import pytest
from flux import task, workflow, ExecutionContext

@task
async def add_numbers(a: int, b: int) -> int:
    return a + b

@workflow
async def calculator_workflow(ctx: ExecutionContext[dict]):
    result = await add_numbers(ctx.input["a"], ctx.input["b"])
    return {"sum": result}

@pytest.mark.asyncio
async def test_calculator_workflow():
    result = calculator_workflow.run({"a": 5, "b": 3})
    assert result.output["sum"] == 8
    assert result.state.value == "completed"
```

## Environment Configuration

### Local Configuration Options

Set environment variables for local development:

```bash
# Enable debug logging
export FLUX_LOG_LEVEL=DEBUG

# Specify custom database location
export FLUX_DATABASE_PATH=./custom_data/flux.db

# Configure local secrets storage
export FLUX_SECRETS_PATH=./custom_data/secrets.db
```

### Configuration File

Create a `flux.toml` for local development:

```toml
[logging]
level = "DEBUG"
format = "text"

[database]
path = ".data/flux.db"

[secrets]
path = ".data/secrets.db"

[development]
auto_reload = true
debug_mode = true
```

## Best Practices

### Local Development Guidelines

1. **Use Direct Execution for Quick Testing**:
   ```python
   if __name__ == "__main__":
       result = my_workflow.run(test_input)
       print(result.output)
   ```

2. **Leverage Local State for Debugging**:
   ```python
   # Always inspect execution context when debugging
   manager = ContextManager.create()
   ctx = manager.get(result.execution_id)
   ```

3. **Test Error Scenarios Locally**:
   ```python
   # Test various failure modes in local environment
   error_result = workflow.run(invalid_input)
   assert error_result.state.value == "failed"
   ```

4. **Use Parallel Tasks for I/O-bound Operations**:
   ```python
   # Even locally, parallel execution improves I/O performance
   results = await parallel(
       fetch_data_1(),
       fetch_data_2(),
       fetch_data_3()
   )
   ```

### When to Use Local vs Distributed Execution

**Use Local Execution For**:
- Development and testing
- Single-machine automation
- Quick prototyping
- CI/CD pipeline tasks
- Data processing that fits in memory

**Switch to Distributed For**:
- Production workloads
- High availability requirements
- Resource-intensive tasks
- Scaling beyond single machine capacity
- Multi-tenant environments

## Next Steps

- **[Server Setup](server-setup.md)** - Learn how to deploy the distributed Flux server
- **[Worker Management](worker-management.md)** - Scale your workflows with worker nodes
- **[Development Workflow](development-workflow.md)** - Best practices for development with Flux
- **[Testing and Debugging](testing-debugging.md)** - Advanced debugging techniques
