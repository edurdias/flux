from examples.fallback import fallback

if __name__ == "__main__":  # pragma: no cover
    ctx = fallback.run()
    print(ctx.to_json())

    replay_ctx = fallback.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
