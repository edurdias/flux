from flux import workflow
from flux.routing import meta, require


@workflow.with_options(
    affinity=require(meta("dispatch.allowed") == "true"),
)
async def metadata_gated(ctx):
    return {"served": True}
