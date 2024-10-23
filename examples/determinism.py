from __future__ import annotations

from flux import workflow
from flux.tasks import now
from flux.tasks import randint
from flux.tasks import randrange
from flux.tasks import uuid4


@workflow
def determinism():
    start = yield now()
    yield uuid4()
    yield randint(1, 5)
    yield randrange(1, 10)
    end = yield now()
    return end - start


if __name__ == "__main__":  # pragma: no cover
    ctx = determinism.run()
    print(ctx.to_json())
