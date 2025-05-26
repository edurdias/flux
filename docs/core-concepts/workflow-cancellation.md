# Workflow Cancellation

Flux provides the ability to cancel running or queued workflows. This feature is essential for managing long-running workflows or when a user wants to abort an execution.

## Cancellation Methods

There are two ways to cancel a workflow:

1. **Pre-execution cancellation**: Cancel a workflow that is queued but not yet executing.
2. **In-flight cancellation**: Cancel a workflow that is currently running.

## Canceling a Workflow

### Using the HTTP API

To cancel a workflow via the API, send a POST request to the cancellation endpoint:

```http
POST /executions/{execution_id}/cancel
```

### Response Examples

Successful cancellation:
```json
{
  "execution_id": "abcd1234",
  "state": "CANCELED",
  "message": "Execution canceled successfully"
}
```

Already finished execution:
```json
{
  "execution_id": "abcd1234",
  "state": "COMPLETED",
  "message": "Execution already finished"
}
```

## Checking Cancellation Status

You can check if a workflow has been canceled using the execution context:

```python
if ctx.has_canceled:
    print("Workflow was canceled")
```

## Writing Cancellation-Aware Workflows

For long-running tasks that should cooperate with cancellation:

```python
@task
async def long_running_task():
    for i in range(100):
        # Periodically check for cancellation
        await ctx.check_cancellation()
        
        # Do some work
        result = await process_item(i)
        
        # Sleep before next iteration
        await asyncio.sleep(1)
```

## Cancellation vs Other Terminal States

Flux executions can end in three terminal states:

- `COMPLETED`: Workflow finished successfully
- `FAILED`: Workflow encountered an error
- `CANCELED`: Workflow was explicitly canceled

All three are considered "finished" states (`ctx.has_finished` will return `True`).

## Implementation Details

Cancellation works by:

1. Setting the execution state to `CANCELED` in the database
2. Triggering an `asyncio.Event` for active workflows
3. Propagating a `CancelationRequested` exception through the workflow

Steps check for cancellation at entry points, allowing even long-running tasks to be cancelled.
