from __future__ import annotations

from datetime import timedelta

from flux import workflow
from flux.tasks import sleep


@workflow
def sleep_workflow():
    yield sleep(timedelta(seconds=2))
    yield sleep(timedelta(seconds=5))
    yield sleep(3.5)


if __name__ == "__main__":  # pragma: no cover
    ctx = sleep_workflow.run()
    print(ctx.to_json())
