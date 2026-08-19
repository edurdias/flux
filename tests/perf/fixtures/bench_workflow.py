"""Synthetic engine workload for the B-series benchmarks (#259).

Three shapes, all CPU-trivial on purpose: these benchmarks measure what the
*engine* costs -- dispatch, checkpointing, replay -- so the task bodies must
not add work of their own that the numbers then attribute to Flux.

- ``bench_chain``: ``tasks`` sequential tasks, each returning ``payload``
  bytes. The unit of throughput, and the event history replay has to walk.
- ``bench_single``: exactly one task. Dispatch latency measured against the
  smallest possible execution, so the figure is the engine's floor rather
  than a workflow's shape.
- ``bench_resumable``: ``tasks`` tasks and then a pause. Replay cost is
  measured by resuming it: every completed task must be short-circuited
  from the log before the workflow can continue.
"""

from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task
async def bench_step(index: int, payload: int) -> str:
    # A payload big enough to exercise the checkpoint write path without
    # making serialization the thing being measured.
    return f"{index}:" + ("x" * payload)


@workflow
async def bench_single(ctx: ExecutionContext[dict]):
    payload = (ctx.input or {}).get("payload", 64)
    return await bench_step(0, payload)


@workflow
async def bench_chain(ctx: ExecutionContext[dict]):
    spec = ctx.input or {}
    tasks = spec.get("tasks", 10)
    payload = spec.get("payload", 64)
    results = []
    for index in range(tasks):
        results.append(await bench_step(index, payload))
    return len(results)


@workflow
async def bench_resumable(ctx: ExecutionContext[dict]):
    spec = ctx.input or {}
    tasks = spec.get("tasks", 50)
    payload = spec.get("payload", 64)
    for index in range(tasks):
        await bench_step(index, payload)
    await pause("replay_gate")
    return tasks
