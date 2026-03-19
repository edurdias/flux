# Task Progress & Streaming

Tasks can report progress during execution using the `progress()` function. Progress events are **ephemeral** — they stream to connected clients in real-time but are never persisted, never stored in the database, and never replayed.

## Quick Start

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import progress

@task
async def process_data(items: list):
    for i, item in enumerate(items):
        result = await transform(item)
        await progress({"processed": i + 1, "total": len(items)})
    return results

@workflow
async def my_workflow(ctx: ExecutionContext):
    return await process_data(ctx.input["items"])
```

When a client connects via stream mode, they receive `TASK_PROGRESS` events:

```bash
curl -N -X POST http://localhost:8000/workflows/my_workflow/run/stream \
    -H "Content-Type: application/json" \
    -d '{"items": [1, 2, 3]}'
```

```
data: {"event": "task.progress", "data": {"task_name": "process_data", "value": {"processed": 1, "total": 3}}}
data: {"event": "task.progress", "data": {"task_name": "process_data", "value": {"processed": 2, "total": 3}}}
data: {"event": "task.progress", "data": {"task_name": "process_data", "value": {"processed": 3, "total": 3}}}
data: {"event": "my_workflow.execution.completed", "data": {"state": "COMPLETED", ...}}
```

## Agent Token Streaming

The `agent()` task uses `progress()` to stream LLM tokens. Streaming is enabled by default:

```python
from flux.tasks.ai import agent

assistant = agent(
    "You are a helpful assistant.",
    model="openai/gpt-4o",
)

result = await assistant("Explain quantum computing")
```

Each token is emitted as `progress({"token": "..."})`. The return value is the complete response.

### Disabling Streaming

```python
assistant = agent(
    "You are a helpful assistant.",
    model="openai/gpt-4o",
    stream=False,
)
```

### Structured Output

When `response_format` is set, streaming is automatically disabled.

```python
from pydantic import BaseModel

class Analysis(BaseModel):
    summary: str
    score: float

analyst = agent(
    "Analyze the text.",
    model="openai/gpt-4o",
    response_format=Analysis,
)
```

## Progress Value

The `progress()` function accepts any value:

```python
await progress({"processed": 500, "total": 10000})
await progress({"step": "building image"})
await progress({"token": "Hello"})
await progress({"phase": "training", "epoch": 3, "loss": 0.042})
```

## Durability Guarantees

Progress events have **zero impact** on Flux's durability model:

| Aspect | Behavior |
|---|---|
| Event log | Progress is never added to `ctx.events` |
| Database | Progress is never persisted |
| Checkpoint | Progress does not trigger checkpoints |
| Replay | Progress events don't exist during replay |
| Event count | Still 2 per task (STARTED + COMPLETED) |

If the worker crashes mid-streaming, the task re-executes on resume. The complete response is durably stored only in the `TASK_COMPLETED` event.

## How It Works

1. Task calls `progress(value)` — enqueued in an in-memory per-execution queue on the worker
2. A background flusher batches and POSTs progress to the server
3. Server buffers in-memory (only if a stream-mode client is connected)
4. SSE handler yields `TASK_PROGRESS` events to the client
5. When no client is listening, progress is silently dropped
