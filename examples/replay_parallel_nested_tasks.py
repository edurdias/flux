from __future__ import annotations

from examples.task_parallel_nested import task_parallel_nested


if __name__ == '__main__':  # pragma: no cover
    repositories = [
        'python/cpython',
        'microsoft/vscode',
        'localsend/localsend',
        'srush/GPU-Puzzles',
        'hyperknot/openfreemap',
    ]
    ctx = task_parallel_nested.run(repositories)
    print(ctx.to_json())

    replay_ctx = task_parallel_nested.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
