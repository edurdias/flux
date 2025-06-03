# Workflow API Reference

The `workflow` class is the core component for defining and executing workflows in Flux.

## Class: `flux.workflow`

### Constructor

```python
workflow(
    func: Callable,
    name: str | None = None,
    secret_requests: list[str] = [],
    output_storage: OutputStorage | None = None,
    requests: ResourceRequest | None = None
)
```

**Parameters:**
- `func`: The async function that implements the workflow logic
- `name`: Optional custom name for the workflow (defaults to function name)
- `secret_requests`: List of secret names required by the workflow
- `output_storage`: Custom storage backend for workflow outputs
- `requests`: Resource requirements for the workflow

### Decorator: `@workflow`

Basic workflow decorator:

```python
@workflow
async def my_workflow(ctx: ExecutionContext[str]) -> str:
    return f"Hello, {ctx.input}"
```

### Decorator: `@workflow.with_options()`

Advanced workflow configuration:

```python
@workflow.with_options(
    name="custom_workflow",
    secret_requests=["API_KEY", "DATABASE_URL"],
    output_storage=custom_storage,
    requests=ResourceRequest.with_cpu(2).with_memory("1GB")
)
async def configured_workflow(ctx: ExecutionContext) -> dict:
    # Workflow implementation
    pass
```

**Parameters:**
- `name`: Custom workflow name
- `secret_requests`: List of required secrets
- `output_storage`: Custom output storage implementation
- `requests`: Resource requirements specification

## Instance Properties

### `name: str`
The name of the workflow (either custom or function name).

### `secret_requests: list[str]`
List of secrets required by this workflow.

### `output_storage: OutputStorage | None`
Custom output storage backend, if configured.

### `requests: ResourceRequest | None`
Resource requirements for the workflow execution.

## Instance Methods

### `run(*args, **kwargs) -> ExecutionContext`

Execute the workflow synchronously.

**Parameters:**
- `*args`: Positional arguments passed to the workflow
- `**kwargs`: Keyword arguments, including:
  - `execution_id`: Optional execution ID to resume existing workflow

**Returns:**
- `ExecutionContext`: The execution context containing workflow state and results

**Example:**
```python
# New execution
ctx = my_workflow.run("input_data")

# Resume existing execution
ctx = my_workflow.run(execution_id="existing_execution_id")
```

### `async __call__(ctx: ExecutionContext, *args) -> ExecutionContext`

Execute the workflow asynchronously (internal method).

**Parameters:**
- `ctx`: The execution context
- `*args`: Additional arguments

**Returns:**
- `ExecutionContext`: Updated execution context

## Workflow Function Signature

All workflow functions must follow this signature:

```python
async def workflow_function(ctx: ExecutionContext[InputType]) -> OutputType:
    # Workflow implementation
    pass
```

**Parameters:**
- `ctx`: Execution context containing workflow state and input data
- Type parameter `[InputType]`: Expected input data type

**Returns:**
- `OutputType`: The workflow result

## Usage Examples

### Basic Workflow
```python
from flux import workflow, ExecutionContext

@workflow
async def hello_workflow(ctx: ExecutionContext[str]) -> str:
    return f"Hello, {ctx.input}"

# Execute
result = hello_workflow.run("World")
print(result.output)  # "Hello, World"
```

### Workflow with Tasks
```python
from flux import workflow, task, ExecutionContext

@task
async def process_data(data: str) -> str:
    return data.upper()

@workflow
async def processing_workflow(ctx: ExecutionContext[str]) -> str:
    result = await process_data(ctx.input)
    return result

# Execute
result = processing_workflow.run("hello")
print(result.output)  # "HELLO"
```

### Workflow with Configuration
```python
@workflow.with_options(
    name="secure_workflow",
    secret_requests=["API_KEY"],
    requests=ResourceRequest.with_cpu(2)
)
async def secure_workflow(ctx: ExecutionContext[dict]) -> dict:
    # Access secrets through execution context
    api_key = ctx.secrets["API_KEY"]

    # Process with guaranteed resources
    result = await heavy_computation(ctx.input)
    return result
```

### Workflow with Pause Points
```python
from flux.tasks import pause

@workflow
async def approval_workflow(ctx: ExecutionContext[str]) -> str:
    # Process data
    processed = await process_data(ctx.input)

    # Pause for manual approval
    await pause("manual_approval")

    # Continue after approval
    return f"Approved: {processed}"

# First execution (runs until pause)
ctx = approval_workflow.run("data")
print(ctx.is_paused)  # True

# Resume execution
ctx = approval_workflow.run(execution_id=ctx.execution_id)
print(ctx.output)  # "Approved: DATA"
```

### Workflow State Inspection
```python
ctx = my_workflow.run("input")

# Check execution state
print(f"Finished: {ctx.has_finished}")
print(f"Succeeded: {ctx.has_succeeded}")
print(f"Failed: {ctx.has_failed}")
print(f"Paused: {ctx.is_paused}")

# Access execution details
print(f"Execution ID: {ctx.execution_id}")
print(f"Input: {ctx.input}")
print(f"Output: {ctx.output}")

# Examine execution events
for event in ctx.events:
    print(f"{event.type}: {event.name}")
```

## Error Handling

Workflows automatically handle errors and maintain state:

```python
@workflow
async def error_handling_workflow(ctx: ExecutionContext[str]) -> str:
    try:
        result = await risky_task(ctx.input)
        return result
    except Exception as e:
        # Handle errors gracefully
        return f"Error: {str(e)}"

# Execute
ctx = error_handling_workflow.run("input")
if ctx.has_failed:
    print(f"Workflow failed: {ctx.output}")
```

## Integration with Tasks

Workflows orchestrate tasks using await:

```python
from flux.tasks import parallel, pipeline

@workflow
async def complex_workflow(ctx: ExecutionContext[list[str]]) -> dict:
    # Parallel execution
    parallel_results = await parallel(
        *[process_item(item) for item in ctx.input]
    )

    # Pipeline processing
    final_result = await pipeline(
        aggregate_results,
        format_output,
        input=parallel_results
    )

    return final_result
```
