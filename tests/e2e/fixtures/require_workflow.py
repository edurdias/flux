from flux import workflow
from flux.routing import input, label, require, when


@workflow.with_options(
    affinity=require(
        label("region") == input("region"),
        when(input("tier") == "dedicated", label("cap.dedicated") == "true"),
    ),
)
async def require_task(ctx):
    return {"served_region": (ctx.input or {}).get("region")}
