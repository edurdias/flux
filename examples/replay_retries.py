from __future__ import annotations

from examples.retries import retries


if __name__ == '__main__':  # pragma: no cover
    ctx = retries.run()
    print(ctx.to_json())

    replay_ctx = retries.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
