"""Fixture workflows for the declared-hooks E2E (declaration path 2).

``declared_hook_source`` mirrors ``declared_hook_notifier`` — its only
task returns what it was handed, so the delivered envelope is readable
straight off the execution's output. ``declared_hook_source`` fails
immediately, which is the event its own ``hooks=`` declaration subscribes
to.
"""

from __future__ import annotations

from typing import Any

from flux import task, workflow
from flux.hooks import hook


@task
async def record(payload: Any) -> Any:
    return payload


@workflow
async def declared_hook_notifier(ctx):
    return await record(ctx.input)


@workflow.with_options(
    namespace="default",
    hooks=[
        hook.run(
            on="execution:default:declared_hook_source:failed",
            workflow="default/declared_hook_notifier",
            principal="e2e-hooks",
        ),
    ],
)
async def declared_hook_source(ctx):
    raise RuntimeError("deliberate failure for the declared-hook e2e")
