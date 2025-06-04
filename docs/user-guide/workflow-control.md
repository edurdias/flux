# Workflow Control

Flux provides sophisticated workflow control capabilities that allow you to pause, resume, inspect, and manage workflow execution dynamically. These features are essential for building robust, debuggable, and maintainable distributed applications.

## Pause and Resume

### Basic Pause Operations

Use the built-in `pause` task to create breakpoints in your workflows:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import pause

@workflow
async def pausable_workflow(ctx: ExecutionContext[str]):
    # Process first phase
    result1 = await process_data(ctx.input)

    # Pause execution with a message
    await pause("Waiting for user approval")

    # Continue after resume
    result2 = await finalize_data(result1)
    return result2
```

### Conditional Pausing

Implement dynamic pause conditions based on runtime state:

```python
@task
async def conditional_pause(data: dict, threshold: float):
    if data.get("confidence", 0) < threshold:
        await pause(f"Low confidence: {data['confidence']}, manual review required")
    return data

@workflow
async def quality_control_workflow(ctx: ExecutionContext[dict]):
    processed = await analyze_data(ctx.input)
    validated = await conditional_pause(processed, threshold=0.8)
    return await final_processing(validated)
```

### Programmatic Resume

Resume workflows programmatically using the HTTP API or CLI:

```bash
# Resume via CLI
flux workflow resume <execution_id>

# Resume with additional context
flux workflow resume <execution_id> --data '{"approval": "granted"}'
```

```python
# Resume via Python API
import httpx

async def resume_workflow(execution_id: str, data: dict = None):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://localhost:8000/executions/{execution_id}/resume",
            json=data or {}
        )
        return response.json()
```

## Deterministic Replay

### Understanding Determinism

Flux ensures deterministic execution by recording all non-deterministic operations and replaying them consistently:

```python
from flux.tasks import now, randint

@workflow
async def deterministic_workflow(ctx: ExecutionContext):
    # These operations are recorded and replayed deterministically
    timestamp = await now()  # Always returns the same time on replay
    random_value = await randint(1, 100)  # Always returns the same value on replay

    return {
        "timestamp": timestamp,
        "random": random_value,
        "input": ctx.input
    }
```

### Replay Scenarios

Replays occur automatically in several scenarios:

1. **Worker Failures**: When a worker crashes, another worker picks up and replays from the last checkpoint
2. **Code Updates**: When workflow code changes, existing executions replay with new logic
3. **Manual Replay**: For debugging or data correction purposes

```python
# Replay example - this workflow will produce identical results
@workflow
async def data_processing_workflow(ctx: ExecutionContext[list]):
    # Generate consistent processing parameters
    seed = await randint(1, 1000)
    processing_time = await now()

    results = []
    for item in ctx.input:
        # Each iteration uses the same seed and timestamp
        processed = await process_item(item, seed=seed, timestamp=processing_time)
        results.append(processed)

    return results
```

## State Inspection

### Execution State Access

Access comprehensive execution state through the execution context:

```python
@task
async def inspect_execution_state(ctx: ExecutionContext):
    execution_info = {
        "execution_id": ctx.execution_id,
        "workflow_name": ctx.workflow_name,
        "current_task": ctx.current_task_name,
        "execution_time": ctx.execution_time,
        "retry_count": ctx.retry_count,
        "event_count": len(ctx.events)
    }

    # Access execution events
    for event in ctx.events:
        print(f"Event: {event.type} at {event.timestamp}")

    return execution_info

@workflow
async def inspectable_workflow(ctx: ExecutionContext[str]):
    # State is automatically tracked
    step1 = await process_step_one(ctx.input)

    # Inspect current state
    state = await inspect_execution_state(ctx)

    step2 = await process_step_two(step1)
    return {"result": step2, "execution_info": state}
```

### Event Streaming

Monitor workflow execution in real-time using event streaming:

```python
import asyncio
import httpx

async def stream_execution_events(execution_id: str):
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "GET",
            f"http://localhost:8000/executions/{execution_id}/events"
        ) as response:
            async for line in response.aiter_lines():
                if line:
                    event = json.loads(line)
                    print(f"Event: {event['type']} - {event['data']}")
```

### State Queries

Query execution state remotely:

```python
async def get_execution_status(execution_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8000/executions/{execution_id}")
        return response.json()

# Example response
{
    "execution_id": "exec_123",
    "status": "running",
    "current_task": "process_data",
    "progress": {
        "completed_tasks": 5,
        "total_tasks": 10,
        "percentage": 50
    },
    "state": {
        "variables": {"user_id": "123", "batch_size": 100},
        "outputs": {"step1": "result1", "step2": "result2"}
    }
}
```

## Event Handling

### Built-in Event Types

Flux generates events for all significant workflow operations:

```python
from flux.domain.events import ExecutionEventType

# Available event types:
# - EXECUTION_STARTED
# - TASK_STARTED
# - TASK_COMPLETED
# - TASK_FAILED
# - EXECUTION_PAUSED
# - EXECUTION_RESUMED
# - EXECUTION_COMPLETED
# - EXECUTION_FAILED

@task
async def event_aware_task(ctx: ExecutionContext):
    # Access recent events
    recent_failures = [
        event for event in ctx.events
        if event.type == ExecutionEventType.TASK_FAILED
    ]

    if len(recent_failures) > 3:
        await pause("Too many failures, manual intervention required")

    return "Processing complete"
```

### Custom Event Handling

Implement custom event handlers for specific workflow needs:

```python
@task
async def custom_event_handler(ctx: ExecutionContext, event_type: str):
    """Custom event processing logic"""
    if event_type == "data_quality_alert":
        # Send notification
        await send_alert_notification(ctx.execution_id)

        # Log detailed information
        await log_quality_metrics(ctx.state)

        # Pause for manual review
        await pause("Data quality alert triggered")

    return "Event handled"

@workflow
async def monitored_workflow(ctx: ExecutionContext[dict]):
    # Process data with monitoring
    result = await process_with_quality_check(ctx.input)

    # Handle quality alerts if triggered
    if result.get("quality_alert"):
        await custom_event_handler(ctx, "data_quality_alert")

    return result
```

### Event-Driven Workflows

Build workflows that react to external events:

```python
@workflow
async def event_driven_workflow(ctx: ExecutionContext[dict]):
    """Workflow that responds to external events"""

    # Wait for external trigger
    await pause("Waiting for external event")

    # Process based on resume data
    event_data = ctx.resume_data

    if event_data.get("event_type") == "user_action":
        return await handle_user_action(event_data)
    elif event_data.get("event_type") == "system_alert":
        return await handle_system_alert(event_data)
    else:
        return await handle_unknown_event(event_data)
```

## Advanced Control Patterns

### Conditional Branching

Implement dynamic workflow paths based on runtime conditions:

```python
@workflow
async def conditional_workflow(ctx: ExecutionContext[dict]):
    input_data = ctx.input

    # Evaluate conditions
    if input_data.get("priority") == "high":
        result = await high_priority_path(input_data)
    elif input_data.get("type") == "batch":
        result = await batch_processing_path(input_data)
    else:
        result = await standard_processing_path(input_data)

    return result
```

### Dynamic Task Creation

Create tasks dynamically based on runtime data:

```python
from flux.tasks import parallel

@workflow
async def dynamic_workflow(ctx: ExecutionContext[list]):
    tasks = []

    # Create tasks based on input data
    for item in ctx.input:
        if item.get("requires_special_processing"):
            tasks.append(special_processor(item))
        else:
            tasks.append(standard_processor(item))

    # Execute all tasks in parallel
    results = await parallel(*tasks)
    return results
```

### Workflow Composition

Combine multiple workflows for complex orchestration:

```python
@workflow
async def composed_workflow(ctx: ExecutionContext[dict]):
    # Execute sub-workflows in sequence
    preprocessed = await preprocessing_workflow(ctx.input)
    analyzed = await analysis_workflow(preprocessed)
    final_result = await postprocessing_workflow(analyzed)

    return {
        "result": final_result,
        "execution_id": ctx.execution_id,
        "total_time": ctx.execution_time
    }
```

## Best Practices

### State Management

1. **Keep State Minimal**: Only store essential data in workflow state
2. **Use Immutable Data**: Prefer immutable data structures for consistency
3. **Regular Checkpoints**: Use pause points to create recovery checkpoints

### Error Recovery

1. **Graceful Degradation**: Design workflows to handle partial failures
2. **State Validation**: Validate state consistency after resume operations
3. **Rollback Capability**: Implement rollback procedures for critical operations

### Performance Optimization

1. **Event Filtering**: Process only relevant events in event handlers
2. **State Queries**: Use efficient state query patterns
3. **Resource Cleanup**: Clean up resources during pause operations

### Monitoring

1. **Comprehensive Logging**: Log all significant state changes
2. **Metric Collection**: Collect performance and business metrics
3. **Alert Integration**: Integrate with monitoring systems for proactive management
