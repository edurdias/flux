"""Fixture workflows for the outbound-hooks E2E.

``hook_gate_e2e`` pauses at an approval gate — the event the first test's
hook subscribes to. ``hook_recorder`` is that hook's target: its only task
returns what it was handed, so the delivered envelope is readable straight
off the execution's output. ``hook_loop`` is the second test's target, for a
hook subscribed to ``hook_loop``'s own completion — a chain nothing but the
hop guard stops.
"""

from __future__ import annotations

from typing import Any

from flux import task, workflow


@task
async def record(payload: Any) -> Any:
    return payload


@task.with_options(requires_approval=True)
async def hook_gate(value: str) -> str:
    return f"released:{value}"


@workflow
async def hook_gate_e2e(ctx):
    return await hook_gate(ctx.input or "default")


@workflow
async def hook_recorder(ctx):
    return await record(ctx.input)


@workflow
async def hook_loop(ctx):
    return await record(ctx.input)
