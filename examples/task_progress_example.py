"""
Task Progress Reporting.

Demonstrates how any @task can report progress during execution using the
progress() primitive. Progress events are ephemeral -- they stream to
connected clients via SSE but are never persisted.

Usage:
    flux workflow run task_progress_example '{"items": 20}'

    curl -N -X POST http://localhost:8000/workflows/task_progress_example/run/stream \\
        -H "Content-Type: application/json" \\
        -d '{"items": 20}'
"""

from __future__ import annotations

import asyncio
from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks import progress


@task
async def process_batch(items: int) -> dict:
    results = []
    for i in range(items):
        await asyncio.sleep(0.1)
        results.append(i * 2)
        await progress({"processed": i + 1, "total": items})
    return {"count": len(results), "sum": sum(results)}


@task
async def multi_step_pipeline(data: dict) -> dict:
    await progress({"step": "validating"})
    await asyncio.sleep(0.2)

    await progress({"step": "transforming"})
    transformed = {k: v * 2 for k, v in data.items() if isinstance(v, (int, float))}
    await asyncio.sleep(0.2)

    await progress({"step": "aggregating"})
    total = sum(transformed.values())
    await asyncio.sleep(0.2)

    await progress({"step": "complete"})
    return {"transformed": transformed, "total": total}


@workflow
async def task_progress_example(ctx: ExecutionContext[dict[str, Any]]):
    input_data = ctx.input or {}
    items = input_data.get("items", 10)

    batch_result = await process_batch(items)
    pipeline_result = await multi_step_pipeline(batch_result)

    return {
        "batch": batch_result,
        "pipeline": pipeline_result,
    }
