# Workflow Controls

## Pause and Resume

Flux allows workflows to be paused and resumed at specific points, enabling interaction or intervention during execution.

### Basic Pause/Resume
```python
from flux import pause, workflow

@workflow
def pause_workflow(ctx: WorkflowExecutionContext[str]):
    # Pause at a specific point
    yield pause("checkpoint_1")  # Reference should be unique

    # Continue execution after resume
    message = yield some_task(ctx.input)
    return message

# Execute workflow until pause
ctx = pause_workflow.run("input_data")
print(ctx.paused)  # True

# Resume execution
ctx = pause_workflow.run(execution_id=ctx.execution_id)
```

### Pause with Input
Wait for user input before continuing:

```python
@workflow
def pause_with_input_workflow():
    # Pause and wait for string input
    name = yield pause("name_input", wait_for_input=str)

    # Continue with provided input
    message = yield say_hello(name)
    return message

# Start workflow
ctx = pause_with_input_workflow.run()
print(ctx.paused)  # True

# Resume with input
ctx = pause_with_input_workflow.run(
    input="Joe",
    execution_id=ctx.execution_id
)
```
## Workflow Replay

Flux automatically handles workflow replay, maintaining consistency and idempotency.

### Deterministic Execution
```python
@workflow
def deterministic_workflow():
    # These tasks will produce the same results
    # when the workflow is replayed
    start = yield now()
    yield uuid4()
    yield randint(1, 5)
    yield randrange(1, 10)
    end = yield now()
    return end - start

# First execution
ctx1 = deterministic_workflow.run()

# Replay execution
ctx2 = deterministic_workflow.run(execution_id=ctx1.execution_id)
# ctx1.output == ctx2.output
```

## Subworkflows

Break down complex workflows into manageable, reusable components using subworkflows.

### Basic Subworkflow
```python
from flux import call_workflow

@workflow
def sub_workflow(ctx: WorkflowExecutionContext[str]):
    result = yield some_task(ctx.input)
    return result

@workflow
def main_workflow(ctx: WorkflowExecutionContext[str]):
    # Call subworkflow
    result = yield call_workflow(sub_workflow, ctx.input)
    return result
```

### Parallel Subworkflows
```python
@workflow
def get_stars_workflow(ctx: WorkflowExecutionContext[str]):
    repo_info = yield get_repo_info(ctx.input)
    return repo_info["stargazers_count"]

@workflow
def parallel_subflows(ctx: WorkflowExecutionContext[list[str]]):
    if not ctx.input:
        raise TypeError("Repository list cannot be empty")

    repos = ctx.input
    stars = {}

    # Execute subworkflows in parallel
    responses = yield get_stars_workflow.map(repos)

    # Collect results
    return {
        repo: response.output
        for repo, response in zip(repos, responses)
    }
```

### Subworkflow Composition
```python
@workflow
def process_workflow(ctx: WorkflowExecutionContext):
    # Sequential subworkflow execution
    data = yield call_workflow(fetch_data_workflow)
    processed = yield call_workflow(transform_workflow, data)
    result = yield call_workflow(save_workflow, processed)
    return result
```
