from flux import ExecutionContext, workflow


@workflow
async def leanness_probe(ctx: ExecutionContext):
    """Reports which heavyweight modules are loaded where user code runs.

    The runner child must not carry the persistence graph (issue #233) or
    the config machinery (issue #241): by the time a workflow body executes,
    sqlalchemy, flux.models, and pydantic must all be absent. Observed from
    inside rather than asserted from outside — this is the real child
    process, whatever runner spawned it.
    """
    import sys

    return {
        "sqlalchemy": "sqlalchemy" in sys.modules,
        "flux_models": "flux.models" in sys.modules,
        "pydantic": "pydantic" in sys.modules,
    }
