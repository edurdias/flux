# Quick Start Guide

## First Workflow

Let's start with a simple "Hello World" workflow:

```python
from flux import task, workflow, WorkflowExecutionContext

@task
async def say_hello(name: str):
    return f"Hello, {name}"

@workflow
async def hello_world(ctx: WorkflowExecutionContext[str]):
    if not ctx.input:
        raise TypeError("Input not provided")
    return await say_hello(ctx.input)
```

This simple example shows:
- A basic task with the `@task` decorator
- A workflow with the `@workflow` decorator
- Basic error handling for missing input
- Usage of `async/await` for asynchronous execution

## Running Workflows

You can run workflows in three different ways:

### 1. Direct Python Execution
```python
# Run the workflow directly
ctx = hello_world.run("World")
print(ctx.output)  # Prints: Hello, World

# Check execution status
print(ctx.finished)   # True if workflow completed
print(ctx.succeeded)  # True if workflow succeeded
```

### 2. Command Line Interface
```bash
# Execute workflow using the CLI
flux exec hello_world.py hello_world "World"
```

### 3. HTTP API
```bash
# Start the API server
flux start examples

# Execute workflow via HTTP
curl --location 'localhost:8000/hello_world' \
     --header 'Content-Type: application/json' \
     --data '"World"'
```
