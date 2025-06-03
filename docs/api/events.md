# Events API Reference

The events system in Flux provides comprehensive tracking and monitoring of workflow and task execution through structured event objects.

## Class: `flux.ExecutionEvent`

### Constructor

```python
ExecutionEvent(
    type: ExecutionEventType,
    source_id: str,
    name: str,
    value: Any = None,
    timestamp: datetime | None = None
)
```

**Parameters:**
- `type`: The type of event (see ExecutionEventType)
- `source_id`: Identifier of the component that generated the event
- `name`: Human-readable name describing the event
- `value`: Optional data associated with the event
- `timestamp`: When the event occurred (auto-generated if not provided)

### Properties

#### `type: ExecutionEventType`
The type of execution event.

#### `source_id: str`
Identifier of the workflow, task, or component that generated this event.

#### `name: str`
Human-readable description of the event.

#### `value: Any`
Optional data payload associated with the event.

#### `timestamp: datetime`
When the event occurred (UTC).

## Enum: `flux.ExecutionEventType`

### Workflow Events

#### `WORKFLOW_STARTED`
Generated when a workflow begins execution.

**Example:**
```python
# Event generated automatically when workflow starts
event = ExecutionEvent(
    type=ExecutionEventType.WORKFLOW_STARTED,
    source_id="my_workflow_12345",
    name="my_workflow",
    value=None
)
```

#### `WORKFLOW_COMPLETED`
Generated when a workflow completes successfully.

**Example:**
```python
# Event with the final result
event = ExecutionEvent(
    type=ExecutionEventType.WORKFLOW_COMPLETED,
    source_id="my_workflow_12345",
    name="my_workflow",
    value="Final workflow result"
)
```

#### `WORKFLOW_FAILED`
Generated when a workflow fails with an error.

**Example:**
```python
# Event with error information
event = ExecutionEvent(
    type=ExecutionEventType.WORKFLOW_FAILED,
    source_id="my_workflow_12345",
    name="my_workflow",
    value="Error: Connection timeout"
)
```

#### `WORKFLOW_PAUSED`
Generated when a workflow is paused.

**Example:**
```python
# Event with pause point name
event = ExecutionEvent(
    type=ExecutionEventType.WORKFLOW_PAUSED,
    source_id="my_workflow_12345",
    name="approval_workflow",
    value="manual_approval"  # pause point name
)
```

#### `WORKFLOW_RESUMED`
Generated when a paused workflow is resumed.

#### `WORKFLOW_CANCELLED`
Generated when a workflow is cancelled.

### Task Events

#### `TASK_STARTED`
Generated when a task begins execution.

#### `TASK_COMPLETED`
Generated when a task completes successfully.

#### `TASK_FAILED`
Generated when a task fails with an error.

#### `TASK_PAUSED`
Generated when a task is paused (rare, mostly for long-running tasks).

### Retry Events

#### `TASK_RETRY_STARTED`
Generated when a task retry attempt begins.

**Example:**
```python
# Event showing retry attempt
event = ExecutionEvent(
    type=ExecutionEventType.TASK_RETRY_STARTED,
    source_id="risky_task_67890",
    name="risky_task",
    value={"attempt": 2, "max_attempts": 3}
)
```

#### `TASK_RETRY_COMPLETED`
Generated when a task retry succeeds.

#### `TASK_RETRY_FAILED`
Generated when a task retry fails.

### Fallback Events

#### `TASK_FALLBACK_STARTED`
Generated when a task's fallback function begins execution.

#### `TASK_FALLBACK_COMPLETED`
Generated when a task's fallback function completes.

#### `TASK_FALLBACK_FAILED`
Generated when a task's fallback function fails.

### Rollback Events

#### `TASK_ROLLBACK_STARTED`
Generated when a task's rollback function begins execution.

#### `TASK_ROLLBACK_COMPLETED`
Generated when a task's rollback function completes.

## Event Usage Examples

### Monitoring Workflow Execution

```python
@workflow
async def monitored_workflow(ctx: ExecutionContext[str]) -> str:
    result = await some_task(ctx.input)
    return result

# Execute workflow
ctx = monitored_workflow.run("input_data")

# Examine all events
for event in ctx.events:
    print(f"{event.timestamp}: {event.type.value} - {event.name}")
    if event.value:
        print(f"  Value: {event.value}")
```

### Filtering Events by Type

```python
# Get only workflow events
workflow_events = [
    event for event in ctx.events
    if event.type.value.startswith("WORKFLOW_")
]

# Get only task events
task_events = [
    event for event in ctx.events
    if event.type.value.startswith("TASK_")
]

# Get only failure events
failure_events = [
    event for event in ctx.events
    if "FAILED" in event.type.value
]
```

### Custom Event Processing

```python
def analyze_execution_events(ctx: ExecutionContext) -> dict:
    """Analyze execution events for performance metrics."""

    start_time = None
    end_time = None
    task_count = 0
    failure_count = 0
    retry_count = 0

    for event in ctx.events:
        if event.type == ExecutionEventType.WORKFLOW_STARTED:
            start_time = event.timestamp
        elif event.type == ExecutionEventType.WORKFLOW_COMPLETED:
            end_time = event.timestamp
        elif event.type == ExecutionEventType.TASK_STARTED:
            task_count += 1
        elif "FAILED" in event.type.value:
            failure_count += 1
        elif "RETRY" in event.type.value:
            retry_count += 1

    duration = (end_time - start_time).total_seconds() if start_time and end_time else None

    return {
        "duration_seconds": duration,
        "task_count": task_count,
        "failure_count": failure_count,
        "retry_count": retry_count,
        "success_rate": (task_count - failure_count) / max(task_count, 1)
    }

# Use the analyzer
ctx = my_workflow.run("data")
metrics = analyze_execution_events(ctx)
print(f"Workflow took {metrics['duration_seconds']} seconds")
print(f"Success rate: {metrics['success_rate']:.2%}")
```

### Event-Driven Monitoring

```python
class WorkflowMonitor:
    def __init__(self):
        self.active_workflows = {}
        self.completed_workflows = {}

    def process_event(self, execution_id: str, event: ExecutionEvent):
        """Process a single execution event."""

        if event.type == ExecutionEventType.WORKFLOW_STARTED:
            self.active_workflows[execution_id] = {
                "start_time": event.timestamp,
                "name": event.name,
                "events": [event]
            }

        elif execution_id in self.active_workflows:
            self.active_workflows[execution_id]["events"].append(event)

            if event.type == ExecutionEventType.WORKFLOW_COMPLETED:
                workflow_data = self.active_workflows.pop(execution_id)
                workflow_data["end_time"] = event.timestamp
                workflow_data["result"] = event.value
                self.completed_workflows[execution_id] = workflow_data

            elif event.type == ExecutionEventType.WORKFLOW_FAILED:
                workflow_data = self.active_workflows.pop(execution_id)
                workflow_data["end_time"] = event.timestamp
                workflow_data["error"] = event.value
                self.completed_workflows[execution_id] = workflow_data

    def get_active_count(self) -> int:
        return len(self.active_workflows)

    def get_average_duration(self) -> float:
        """Calculate average workflow duration."""
        durations = []
        for workflow in self.completed_workflows.values():
            if "end_time" in workflow:
                duration = (workflow["end_time"] - workflow["start_time"]).total_seconds()
                durations.append(duration)

        return sum(durations) / len(durations) if durations else 0.0

# Usage
monitor = WorkflowMonitor()

# Process events from multiple workflows
for execution_id, ctx in workflow_executions.items():
    for event in ctx.events:
        monitor.process_event(execution_id, event)

print(f"Active workflows: {monitor.get_active_count()}")
print(f"Average duration: {monitor.get_average_duration():.2f} seconds")
```

### Debugging with Events

```python
def debug_workflow_execution(ctx: ExecutionContext):
    """Debug helper to analyze workflow execution."""

    print(f"Workflow: {ctx.workflow_name}")
    print(f"Execution ID: {ctx.execution_id}")
    print(f"Final State: {ctx.state}")
    print("\nExecution Timeline:")
    print("-" * 50)

    for i, event in enumerate(ctx.events):
        indent = "  " if event.type.value.startswith("TASK_") else ""
        print(f"{i+1:2d}. {indent}{event.timestamp.strftime('%H:%M:%S.%f')[:-3]} - {event.type.value}")
        print(f"    {indent}Source: {event.source_id}")
        print(f"    {indent}Name: {event.name}")
        if event.value:
            print(f"    {indent}Value: {event.value}")
        print()

# Debug a failed workflow
ctx = problematic_workflow.run("test_data")
debug_workflow_execution(ctx)
```

### Real-time Event Streaming

```python
import asyncio
from typing import AsyncGenerator

async def stream_workflow_events(ctx: ExecutionContext) -> AsyncGenerator[ExecutionEvent, None]:
    """Stream workflow events in real-time."""

    last_event_count = 0

    while not ctx.has_finished:
        # Check for new events
        current_event_count = len(ctx.events)
        if current_event_count > last_event_count:
            # Yield new events
            for event in ctx.events[last_event_count:]:
                yield event
            last_event_count = current_event_count

        # Small delay to avoid busy waiting
        await asyncio.sleep(0.1)

    # Yield any final events
    for event in ctx.events[last_event_count:]:
        yield event

# Usage with async iteration
async def monitor_workflow():
    ctx = long_running_workflow.run("data")

    async for event in stream_workflow_events(ctx):
        print(f"Live event: {event.type.value} - {event.name}")

        # React to specific events
        if event.type == ExecutionEventType.WORKFLOW_PAUSED:
            print(f"Workflow paused at: {event.value}")
        elif event.type == ExecutionEventType.TASK_FAILED:
            print(f"Task failed: {event.value}")
```

### Event Serialization

```python
def serialize_events(events: list[ExecutionEvent]) -> list[dict]:
    """Serialize events for storage or transmission."""

    return [
        {
            "type": event.type.value,
            "source_id": event.source_id,
            "name": event.name,
            "value": event.value,
            "timestamp": event.timestamp.isoformat()
        }
        for event in events
    ]

def deserialize_events(event_data: list[dict]) -> list[ExecutionEvent]:
    """Deserialize events from stored data."""

    from datetime import datetime

    return [
        ExecutionEvent(
            type=ExecutionEventType(data["type"]),
            source_id=data["source_id"],
            name=data["name"],
            value=data.get("value"),
            timestamp=datetime.fromisoformat(data["timestamp"])
        )
        for data in event_data
    ]

# Example usage
ctx = my_workflow.run("data")
event_data = serialize_events(ctx.events)

# Save to JSON file
import json
with open("workflow_events.json", "w") as f:
    json.dump(event_data, f, indent=2)

# Later, load and deserialize
with open("workflow_events.json", "r") as f:
    saved_data = json.load(f)

events = deserialize_events(saved_data)
```

## Integration with External Systems

### Logging Integration

```python
import logging

def setup_event_logging():
    """Setup logging for workflow events."""

    logger = logging.getLogger("flux.events")
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    return logger

def log_workflow_events(ctx: ExecutionContext):
    """Log all workflow events."""

    logger = setup_event_logging()

    for event in ctx.events:
        log_level = logging.ERROR if "FAILED" in event.type.value else logging.INFO
        logger.log(
            log_level,
            f"{event.type.value} - {event.name} (Source: {event.source_id})"
        )
```

### Metrics Collection

```python
def collect_workflow_metrics(ctx: ExecutionContext) -> dict:
    """Collect metrics from workflow events."""

    metrics = {
        "total_events": len(ctx.events),
        "workflow_duration": 0,
        "task_durations": {},
        "error_count": 0,
        "retry_count": 0
    }

    workflow_start = None
    workflow_end = None
    task_starts = {}

    for event in ctx.events:
        # Track workflow timing
        if event.type == ExecutionEventType.WORKFLOW_STARTED:
            workflow_start = event.timestamp
        elif event.type == ExecutionEventType.WORKFLOW_COMPLETED:
            workflow_end = event.timestamp

        # Track task timing
        elif event.type == ExecutionEventType.TASK_STARTED:
            task_starts[event.source_id] = event.timestamp
        elif event.type == ExecutionEventType.TASK_COMPLETED:
            if event.source_id in task_starts:
                duration = (event.timestamp - task_starts[event.source_id]).total_seconds()
                metrics["task_durations"][event.name] = duration

        # Count errors and retries
        elif "FAILED" in event.type.value:
            metrics["error_count"] += 1
        elif "RETRY" in event.type.value:
            metrics["retry_count"] += 1

    # Calculate total workflow duration
    if workflow_start and workflow_end:
        metrics["workflow_duration"] = (workflow_end - workflow_start).total_seconds()

    return metrics
```
