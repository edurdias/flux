from __future__ import annotations

import time

from flux import task
from flux import workflow


def fallback_for_long_task(number):
    print(f"Fallback for task #{number}")
    return f"Task #{number} fellback."


@task.with_options(fallback=fallback_for_long_task, timeout=3)
def bad_task(number: int, should_take_time: bool = True):
    if should_take_time:
        print(f"Long task #{number}")
        time.sleep(5)
    print(f"Succeed task #{number}")
    return f"Task #{number} succeed."


@workflow
def fallback_after_timeout():
    yield bad_task(1)
    yield bad_task(2, False)  # will pass
    yield bad_task(3)


if __name__ == "__main__":  # pragma: no cover
    ctx = fallback_after_timeout.run()
    print(ctx.to_json())
