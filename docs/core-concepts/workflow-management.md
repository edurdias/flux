# Workflow Management

## Creating Workflows

A workflow in Flux is created by combining the `@workflow` decorator with a Python generator function that yields tasks. Workflows are the primary way to organize and orchestrate task execution.

```python
from flux import workflow, WorkflowExecutionContext, task

@task
def process_data(data: str):
    return data.upper()

@workflow
def my_workflow(ctx: WorkflowExecutionContext[str]):
    # Workflows must take a WorkflowExecutionContext as first argument
    # The type parameter [str] indicates the expected input type
    result = yield process_data(ctx.input)
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
def configured_workflow(ctx: WorkflowExecutionContext):
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
   def lifecycle_example(ctx: WorkflowExecutionContext):
       # Start event is generated
       first_result = yield task_one()    # Task execution
       second_result = yield task_two()    # Next task
       return second_result                # Completion
   ```

3. **Completion or Failure**
   ```python
   # Check workflow status
   if ctx.finished:
       if ctx.succeeded:
           print(f"Success: {ctx.output}")
       elif ctx.failed:
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
   print(ctx.finished)  # False while running
   ```

2. **Paused**
   ```python
   @workflow
   def pausable_workflow(ctx: WorkflowExecutionContext):
       yield pause("checkpoint")  # Workflow pauses here
       yield next_task()

   ctx = pausable_workflow.run()
   print(ctx.paused)  # True when paused
   ```

3. **Completed**
   ```python
   # Successfully completed
   print(ctx.finished and ctx.succeeded)  # True
   print(ctx.output)  # Contains workflow result
   ```

4. **Failed**
   ```python
   # Failed execution
   print(ctx.finished and ctx.failed)  # True
   print(ctx.output)  # Contains error information
   ```
