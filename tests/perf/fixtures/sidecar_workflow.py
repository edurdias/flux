"""T4 fixture: tee a sidecar's SSE token stream into ``progress()``.

The sidecar embeds its emit timestamp in every token; the workflow passes it
through untouched as the frame's ``t``, so a Flux consumer's latency and a
direct sidecar consumer's latency measure from the same instant — their
difference is exactly the overhead Flux adds.
"""

from __future__ import annotations

import json

import httpx

from flux import ExecutionContext, task, workflow
from flux.tasks import progress


@task
async def tee_tokens(url: str, tokens: int, gap_ms: float) -> int:
    count = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
        async with client.stream(
            "GET",
            url,
            params={"tokens": tokens, "gap_ms": gap_ms},
        ) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                event = json.loads(data)
                await progress({"i": event["i"], "t": event["t"], "token": event["token"]})
                count += 1
    return count


@workflow
async def perf_sidecar_stream(ctx: ExecutionContext):
    params = ctx.input or {}
    count = await tee_tokens(
        url=params["url"],
        tokens=int(params.get("tokens", 100)),
        gap_ms=float(params.get("gap_ms", 33.0)),
    )
    return {"tokens": count}
