# Workflow Management

## Creating Workflows

A workflow in Flux is created by combining the `@workflow` decorator with a Python async function that uses await for tasks. Workflows are the primary way to organize and orchestrate task execution.

```python
from flux import workflow, ExecutionContext, task

@task
async def process_data(data: str):
    return data.upper()

@workflow
async def my_workflow(ctx: ExecutionContext[str]):
    # Workflows must take an ExecutionContext as first argument
    # The type parameter [str] indicates the expected input type
    result = await process_data(ctx.input)
    return result
```

### Workflow Configuration

Workflows can be configured using `with_options`:

```python
@workflow.with_options(
    name="custom_workflow",              # Custom name for the workflow
    secret_requests=["API_KEY"],         # Secrets required by the workflow
    output_storage=custom_storage        # Custom storage for workflow outputs
)
async def configured_workflow(ctx: ExecutionContext):
    pass
```

## Workflow Lifecycle

A workflow goes through several stages during its execution:

1. **Initialization**
   ```python
   # Workflow is registered with a unique execution ID
   ctx = my_workflow.run("input_data")
   ```

2. **Execution**
   ```python
   @workflow
   async def lifecycle_example(ctx: ExecutionContext):
       # Start event is generated
       first_result = await task_one()    # Task execution
       second_result = await task_two()    # Next task
       return second_result                # Completion
   ```

3. **Completion or Failure**
   ```python
   # Check workflow status
   if ctx.has_finished:
       if ctx.has_succeeded:
           print(f"Success: {ctx.output}")
       elif ctx.has_failed:
           print(f"Failed: {ctx.output}")  # Contains error information
   ```

4. **Replay or Resume** (if needed)
   ```python
   # Resume a previous execution
   ctx = my_workflow.run(execution_id=previous_execution_id)
   ```

## Workflow States

Workflows can be in several states:

1. **Running**
   ```python
   ctx = my_workflow.run("input")
   print(ctx.has_finished)  # False while running
   ```

2. **Paused**
   ```python
   # Workflow with pause point
   from flux.tasks import pause

   @workflow
   async def pausable_workflow(ctx: ExecutionContext):
       await some_task()
       await pause("manual_approval")
       return "Complete"

   ctx = pausable_workflow.run()
   print(ctx.is_paused)  # True when paused
   print(ctx.has_finished)  # False when paused

   # Resume paused workflow
   ctx = pausable_workflow.run(execution_id=ctx.execution_id)
   ```

3. **Completed**
   ```python
   # Successfully completed
   print(ctx.has_finished and ctx.has_succeeded)  # True
   print(ctx.output)  # Contains workflow result
   ```

4. **Failed**
   ```python
   # Failed execution
   print(ctx.has_finished and ctx.has_failed)  # True
   print(ctx.output)  # Contains error information
   ```
