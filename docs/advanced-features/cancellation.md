# Flux Workflow Cancellation

This document provides information about the cancellation feature in Flux.

## Overview

The cancellation feature allows you to cancel workflows that are currently running. This is useful for long-running workflows that you want to stop before they complete.

## Cancellation States

A workflow can be in one of the following cancellation-related states:

- `CANCELLING` - The workflow is in the process of being cancelled
- `CANCELLED` - The workflow has been successfully cancelled

## How to Use

### API Endpoint

You can cancel a workflow using the API endpoint:

```
GET /workflows/{workflow_name}/cancel/{execution_id}?mode=async
```

Parameters:
- `workflow_name` - The name of the workflow
- `execution_id` - The ID of the workflow execution
- `mode` - Either `sync` or `async` (defaults to `async`)
  - `sync` - Waits for the cancellation to complete before responding
  - `async` - Initiates the cancellation and returns immediately

### Command Line Interface

You can also cancel workflows using the Flux CLI:

```bash
# Asynchronous cancellation (returns immediately)
flux workflow cancel <workflow_name> <execution_id>

# Synchronous cancellation (waits for the cancellation to complete)
flux workflow cancel <workflow_name> <execution_id> --sync
```

This command will send a cancellation request to the server and display the current status of the workflow.

## Example

See `examples/cancellation.py` for a complete example of how to cancel a workflow.

Run the example:

```bash
python examples/cancellation.py
```

This example demonstrates:
1. Starting a long-running workflow
2. Requesting cancellation after 5 seconds
3. Handling the cancellation in the workflow

## Testing

To run the tests for the cancellation feature:

```bash
pytest tests/flux/test_cancellation_integration.py
pytest tests/flux/domain/test_execution_context.py
pytest tests/flux/test_context_manager_cancellation.py
pytest tests/flux/test_worker_cancellation.py
pytest tests/flux/test_server_cancellation.py
pytest tests/examples/test_cancellation.py
```

## Implementation Details

The cancellation feature works as follows:

1. When a cancellation is requested, the execution context is put into the `CANCELLING` state
2. The server notifies the worker that is executing the workflow
3. The worker cancels the asyncio task that is running the workflow
4. The workflow catches the `asyncio.CancelledError` and updates its state to `CANCELLED`
5. The worker sends a checkpoint back to the server with the final state

This approach ensures that the workflow is cancelled cleanly and all resources are properly released.

The server re-sends the cancellation on every dispatch cycle for as long as the
execution remains `CANCELLING`, so delivery edge cases resolve rather than park:

- **The terminal write survives a second cancellation.** The checkpoint that
  persists `CANCELLED` runs shielded with a bounded wait; a further
  cancellation arriving mid-write cannot discard it. If the wait ends before
  the write lands, the write continues detached and its outcome is logged.
- **A worker that is not running the execution resolves it.** If the notified
  worker has no matching running task — for example, it restarted after the
  claim — it checkpoints the execution as `CANCELLED` itself. Executions
  already in a terminal state are never rewritten; the server rejects state
  writes to finished executions.
- **A claim in flight defers.** Between claiming an execution and starting it,
  the worker declines to resolve a cancellation and waits for the next
  delivery, which either cancels the now-running task or resolves the row if
  the claim failed.
- **A cancellation with no live delivery target resolves from the scheduler.**
  An execution cancelled while parked (never dispatched, so no worker to
  notify) resolves on the next scheduler tick. One assigned to a worker that
  never reconnects resolves after `[flux.workers] cancellation_orphan_grace`
  (default 300s; 0 waits forever) — the grace gives a worker in reconnect
  backoff the chance to resolve its own row, which interrupts the running
  body rather than abandoning it.
