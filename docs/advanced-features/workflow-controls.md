# Workflow Controls


## Workflow Replay

Flux automatically handles workflow replay, maintaining consistency and idempotency.

### Deterministic Execution
```python
@workflow
async def deterministic_workflow():
    # These tasks will produce the same results
    # when the workflow is replayed
    start = await now()
    await uuid4()
    await randint(1, 5)
    await randrange(1, 10)
    end = await now()
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
from flux import call

@workflow
async def sub_workflow(ctx: WorkflowExecutionContext[str]):
    result = await some_task(ctx.input)
    return result

@workflow
async def main_workflow(ctx: WorkflowExecutionContext[str]):
    # Call subworkflow
    result = await call(sub_workflow, ctx.input)
    return result
```

### Parallel Subworkflows
```python
@workflow
async def get_stars_workflow(ctx: WorkflowExecutionContext[str]):
    repo_info = await get_repo_info(ctx.input)
    return repo_info["stargazers_count"]

@workflow
async def parallel_subflows(ctx: WorkflowExecutionContext[list[str]]):
    if not ctx.input:
        raise TypeError("Repository list cannot be empty")

    repos = ctx.input
    stars = {}

    # Execute subworkflows in parallel
    responses = await get_stars_workflow.map(repos)

    # Collect results
    return {
        repo: response.output
        for repo, response in zip(repos, responses)
    }
```

### Subworkflow Composition
```python
@workflow
async def process_workflow(ctx: WorkflowExecutionContext):
    # Sequential subworkflow execution
    data = await call(fetch_data_workflow)
    processed = await call(transform_workflow, data)
    result = await call(save_workflow, processed)
    return result
```
