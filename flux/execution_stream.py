"""The SSE frame loop for one execution stream.

Extracted from ``flux.server`` (#264 stage 3). A consumer holding this
generator open sees two interleaved sources: *progress* frames arriving
through the execution's bounded buffer, and *state* frames produced by
re-hydrating the context whenever the persisted event log advances. The
loop exists to merge them onto one wire without letting either starve the
other, and to leave no claim behind when the consumer disconnects.

Its input is an :class:`~flux.execution_signals.ExecutionSignals` and a
``ContextManager`` -- no server, no app.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from flux.context_managers import ContextManager
from flux.domain import ExecutionContext
from flux.execution_signals import ExecutionSignals
from flux.servers.models import redacted_response
from flux.utils import get_logger, to_wire_json

logger = get_logger(__name__)


async def stream_execution_events(
    signals: ExecutionSignals,
    ctx: ExecutionContext,
    manager: ContextManager,
    detailed: bool,
    emit_initial: bool = False,
) -> AsyncIterator[dict]:
    event = signals.event_for(ctx.execution_id)
    progress_buffer = signals.progress_buffer(ctx.execution_id)
    active_tasks: set[asyncio.Task] = set()
    # Cheap change signal: the loop re-hydrates the full context (all
    # events, unpickled) only when the persisted event log has actually
    # advanced past what `ctx` already holds. The hydrated flag (not the
    # ordinal itself) marks "seen at least once" so an execution with an
    # empty event log (ordinal None) also skips repeat hydration.
    #
    # Whether to *emit* the hydrated context is then decided by the last
    # event's identity, never by its timestamp. Events are stamped on
    # whichever machine creates them, so any backwards clock movement — a
    # worker mid-upgrade, an NTP step — used to make every later event
    # compare as older; since the ordinal had already advanced past it,
    # the context was never refreshed again and the stream hung open.
    last_seen_ordinal: int | None = None
    hydrated_once = False

    def _get_if_changed() -> ExecutionContext | None:
        nonlocal last_seen_ordinal, hydrated_once
        ordinal = manager.last_event_ordinal(ctx.execution_id)
        if hydrated_once and ordinal == last_seen_ordinal:
            return None
        last_seen_ordinal = ordinal
        hydrated_once = True
        return manager.get(ctx.execution_id)

    if emit_initial:
        # Emit the current state immediately so a consumer attaching after
        # the execution already finished still receives the terminal
        # frame — the loop below exits at once when ctx.has_finished.
        yield {
            "event": f"{ctx.workflow_name}.execution.{ctx.state.value.lower()}",
            "data": to_wire_json(await redacted_response(ctx, detailed=detailed)),
        }
    try:
        while not ctx.has_finished:
            if progress_buffer:
                progress_task = asyncio.create_task(progress_buffer.get())
                checkpoint_task = asyncio.create_task(event.wait())
                active_tasks = {progress_task, checkpoint_task}

                done, pending = await asyncio.wait(
                    active_tasks,
                    timeout=30.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                active_tasks.clear()

                if not done:
                    continue

                if progress_task in done:
                    item = progress_task.result()
                    items = [item]
                    while not progress_buffer.empty():
                        items.append(progress_buffer.get_nowait())
                    for p in items:
                        yield {
                            "event": "task.progress",
                            "data": to_wire_json(
                                {
                                    "type": p.type.value,
                                    "source_id": p.source_id,
                                    "name": p.name,
                                    "value": p.value,
                                    "time": str(p.time),
                                },
                            ),
                        }

                if checkpoint_task in done or event.is_set():
                    event.clear()
                    new_ctx = _get_if_changed()
                    if (
                        new_ctx is not None
                        and new_ctx.events
                        and (not ctx.events or new_ctx.events[-1].id != ctx.events[-1].id)
                    ):
                        ctx = new_ctx
                        yield {
                            "event": f"{ctx.workflow_name}.execution.{ctx.state.value.lower()}",
                            "data": to_wire_json(
                                await redacted_response(ctx, detailed=detailed),
                            ),
                        }
            else:
                try:
                    await asyncio.wait_for(event.wait(), timeout=30.0)
                except TimeoutError:
                    pass
                event.clear()
                new_ctx = _get_if_changed()
                if (
                    new_ctx is not None
                    and new_ctx.events
                    and (not ctx.events or new_ctx.events[-1].id != ctx.events[-1].id)
                ):
                    ctx = new_ctx
                    yield {
                        "event": f"{ctx.workflow_name}.execution.{ctx.state.value.lower()}",
                        "data": to_wire_json(await redacted_response(ctx, detailed=detailed)),
                    }
    finally:
        for t in active_tasks:
            if not t.done():
                t.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        signals.drop_event(ctx.execution_id)
        signals.drop_progress_buffer(ctx.execution_id)
        try:
            from flux.domain import ExecutionState as _ExecutionState

            latest = manager.get(ctx.execution_id)
            if latest and latest.state == _ExecutionState.RESUME_SCHEDULED:
                manager.unclaim(ctx.execution_id)
                logger.info(
                    f"SSE disconnect: reverted {ctx.execution_id} from "
                    f"RESUME_SCHEDULED back to RESUMING",
                )
        except Exception as exc:
            logger.warning(
                f"Failed to revert RESUME_SCHEDULED on SSE disconnect for "
                f"{ctx.execution_id}: {exc}",
            )
