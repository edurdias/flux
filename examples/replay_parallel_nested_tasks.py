from examples.task_parallell_nested import task_parallell_nested


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = task_parallell_nested.run(repositories)
    print(ctx.to_json())

    replay_ctx = task_parallell_nested.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
