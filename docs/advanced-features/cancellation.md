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
