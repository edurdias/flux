from examples.task_parallel import task_parallel


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = task_parallel.run(repositories)
    print(ctx.to_json())

    replay_ctx = task_parallel.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
