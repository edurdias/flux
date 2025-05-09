from __future__ import annotations

from flux import workflow
from flux.context import WorkflowExecutionContext
from flux.tasks import now
from flux.tasks import randint
from flux.tasks import randrange
from flux.tasks import uuid4


@workflow
async def determinism(ctx: WorkflowExecutionContext):
    start = await now()
    await uuid4()
    await randint(1, 5)
    await randrange(1, 10)
    end = await now()
    return end - start


if __name__ == "__main__":  # pragma: no cover
    ctx = determinism.run()
    print(ctx.to_json())
