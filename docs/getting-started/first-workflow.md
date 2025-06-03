# Your First Workflow

Welcome to building your first workflow with Flux! This guide will walk you through creating a simple "Hello World" example in just a few minutes.

## What You'll Learn

By the end of this guide, you'll understand:
- How to create a task using the `@task` decorator
- How to build a workflow using the `@workflow` decorator
- How to run a workflow and see the results

## Prerequisites

Before starting, make sure you have Flux installed. If you haven't installed it yet, check out the [Installation Guide](installation.md).

## Creating Your First Workflow

Create a new Python file called `hello_world.py` and add the following code:

```python
from flux import ExecutionContext, task, workflow

@task
async def say_hello(name: str):
    return f"Hello, {name}!"

@workflow
async def hello_world(ctx: ExecutionContext[str]):
    greeting = await say_hello(ctx.input)
    return greeting

if __name__ == "__main__":
    result = hello_world.run("World")
    print(result.output)  # Output: Hello, World!
```

That's it! You've created your first Flux workflow.

## Understanding the Code

Let's break down what's happening:

### The Task
```python
@task
async def say_hello(name: str):
    return f"Hello, {name}!"
```
- `@task` marks this function as a Flux task
- Tasks are the basic units of work in your workflows
- This task takes a name and returns a greeting

### The Workflow
```python
@workflow
async def hello_world(ctx: ExecutionContext[str]):
    greeting = await say_hello(ctx.input)
    return greeting
```
- `@workflow` marks this function as a Flux workflow
- Workflows orchestrate tasks and define the overall flow
- `ctx.input` contains the data passed to the workflow
- Use `await` to call tasks within workflows

### Running the Workflow
```python
result = hello_world.run("World")
print(result.output)
```
- `.run()` executes the workflow with the given input
- The result contains the workflow's output and execution information

## Running Your Workflow

There are multiple ways to run your workflow:

### Method 1: Direct Python Execution

Save the file and run it directly:

```bash
python hello_world.py
```

You should see:
```
Hello, World!
```

You can also test it with different inputs:

```python
# Try different names
result1 = hello_world.run("Alice")
print(result1.output)  # Hello, Alice!

result2 = hello_world.run("Bob")
print(result2.output)  # Hello, Bob!
```

### Method 2: Using Flux CLI (Distributed)

For production workloads or to take advantage of distributed execution, you can use the Flux CLI:

#### Step 1: Start the Server
In one terminal, start the Flux server:

```bash
flux start server
```

The server coordinates workflow execution and manages worker nodes.

#### Step 2: Start a Worker
In another terminal, start a worker node:

```bash
flux start worker
```

Workers automatically connect to the server and register themselves for task execution.

#### Step 3: Register Your Workflow
Register your workflow file with the server:

```bash
flux workflow register hello_world.py
```

#### Step 4: Run the Workflow
Execute your workflow using the CLI:

```bash
# Asynchronous execution (returns immediately)
flux workflow run hello_world '"World"'

# Synchronous execution (waits for completion)
flux workflow run hello_world '"World"' --mode sync

# Streaming execution (real-time updates)
flux workflow run hello_world '"World"' --mode stream
```

**Note:** The input parameter needs to be properly quoted. For strings, use double quotes around single quotes: `'"World"'`

## Adding More Tasks

Let's extend our workflow with another task:

```python
from flux import ExecutionContext, task, workflow

@task
async def say_hello(name: str):
    return f"Hello, {name}!"

@task
async def add_timestamp():
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")

@workflow
async def enhanced_hello_world(ctx: ExecutionContext[str]):
    greeting = await say_hello(ctx.input)
    timestamp = await add_timestamp()
    return f"{greeting} (at {timestamp})"

if __name__ == "__main__":
    result = enhanced_hello_world.run("World")
    print(result.output)  # Hello, World! (at 14:30:15)
```

This shows how workflows can orchestrate multiple tasks to create more complex behavior.

## Next Steps

Congratulations! You've created your first Flux workflow. Now you're ready to explore more advanced features:

1. **[Basic Concepts](basic-concepts.md)** - Learn more about tasks, workflows, and execution context
2. **[Task Options](task-options.md)** - Discover retry, timeout, and error handling options
3. **[Parallel Execution Tutorial](tutorials/parallel-execution.md)** - Run multiple tasks at the same time
4. **[Pipeline Processing Tutorial](tutorials/pipeline-processing.md)** - Chain tasks together in sequence
