# Basic Examples

This section covers fundamental Flux workflows that demonstrate core concepts and basic usage patterns.

## Hello World

The simplest possible Flux workflow demonstrates the basic structure of tasks and workflows.

**File:** `examples/hello_world.py`

```python
from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow

@task
async def say_hello(name: str):
    return f"Hello, {name}"

@workflow
async def hello_world(ctx: ExecutionContext[str]):
    if not ctx.input:
        raise TypeError("Input not provided")
    return await say_hello(ctx.input)

if __name__ == "__main__":
    ctx = hello_world.run("Joe")
    print(ctx.to_json())
```

**Key concepts demonstrated:**
- Basic task definition with `@task` decorator
- Simple workflow definition with `@workflow` decorator
- Input validation in workflows
- Synchronous workflow execution with `.run()`

## Simple Pipeline

A basic pipeline that processes data through sequential steps.

**File:** `examples/simple_pipeline.py`

```python
from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow
from flux.tasks import pipeline

@task
async def step_one(data: int) -> int:
    return data * 2

@task
async def step_two(data: int) -> int:
    return data + 10

@task
async def step_three(data: int) -> str:
    return f"Result: {data}"

@workflow
async def simple_pipeline_workflow(ctx: ExecutionContext[int]):
    return await pipeline(
        step_one(ctx.input),
        step_two,
        step_three
    )
```

**Key concepts demonstrated:**
- Sequential task execution with `pipeline()`
- Data transformation through multiple steps
- Type-safe data flow between tasks

## Using Secrets

Demonstrates how to securely handle sensitive data in workflows.

**File:** `examples/using_secrets.py`

**Key concepts demonstrated:**
- Secure handling of sensitive configuration
- Environment variable integration
- Safe credential management in workflows

## Sleep Task

A simple example showing asynchronous operations and timing.

**File:** `examples/sleep.py`

**Key concepts demonstrated:**
- Asynchronous task execution
- Time-based operations
- Non-blocking workflow patterns

## Running Basic Examples

To run any of these examples:

```bash
# From the project root directory
python examples/hello_world.py
python examples/simple_pipeline.py
python examples/using_secrets.py
python examples/sleep.py
```

Each example is self-contained and demonstrates specific Flux features in isolation, making them perfect for learning the fundamentals.
