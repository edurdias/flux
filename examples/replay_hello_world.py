from __future__ import annotations

from examples.hello_world import hello_world


if __name__ == "__main__":  # pragma: no cover
    ctx = hello_world.run("Joe")
    print(ctx.to_json())

    replay_ctx = hello_world.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
