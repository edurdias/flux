from flux import workflow


@workflow
async def lost_frame_task(ctx):
    return "lost_frame_task_done"
