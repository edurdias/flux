"""Cancelled tasks record TASK_CANCELLED and run their rollback (issue #149).

The settled semantics under test:

- A task interrupted by cancellation appends an audit-only ``TASK_CANCELLED``
  event and re-raises — the event is invisible to the replay short-circuit,
  so the task re-runs on resume.
- A declared rollback runs (shielded, bounded); retry and fallback are
  skipped — a fallback would record a substitute ``TASK_COMPLETED`` that
  replay would honor forever.
- A cancel that lands after the terminal event was appended is a no-op.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from flux import ExecutionContext
from flux._concurrency import gather_batch
from flux.domain.events import ExecutionEventType
from flux.task import task
from flux.tasks import pause
from flux.workflow import workflow


async def _checkpoint(context):
    return context


def _make_ctx(name: str) -> ExecutionContext:
    ctx: ExecutionContext = ExecutionContext(
        workflow_id=name,
        workflow_namespace="default",
        workflow_name=name,
        input=None,
    )
    ctx.set_checkpoint(_checkpoint)
    return ctx


def _events(ctx, event_type):
    return [e for e in ctx.events if e.type == event_type]


class TestCancelledTaskEvents:
    @pytest.mark.asyncio
    async def test_cancelled_task_records_task_cancelled_event(self):
        @task
        async def hang():
            await asyncio.sleep(30)

        @workflow
        async def wf(ctx: ExecutionContext):
            await hang()

        ctx = _make_ctx("wf_cancel_event")
        run = asyncio.create_task(wf(ctx))
        await asyncio.sleep(0.2)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        cancelled = _events(ctx, ExecutionEventType.TASK_CANCELLED)
        assert len(cancelled) == 1
        assert cancelled[0].name == "hang"
        # No fabricated terminal outcome.
        assert not _events(ctx, ExecutionEventType.TASK_COMPLETED)
        assert not _events(ctx, ExecutionEventType.TASK_FAILED)

    @pytest.mark.asyncio
    async def test_rollback_runs_on_cancellation(self):
        rolled_back = asyncio.Event()

        async def compensate():
            rolled_back.set()
            return "compensated"

        @task.with_options(rollback=compensate)
        async def hang():
            await asyncio.sleep(30)

        @workflow
        async def wf(ctx: ExecutionContext):
            await hang()

        ctx = _make_ctx("wf_cancel_rollback")
        run = asyncio.create_task(wf(ctx))
        await asyncio.sleep(0.2)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert rolled_back.is_set()
        assert len(_events(ctx, ExecutionEventType.TASK_ROLLBACK_STARTED)) == 1
        assert len(_events(ctx, ExecutionEventType.TASK_ROLLBACK_COMPLETED)) == 1
        # TASK_CANCELLED precedes the rollback events in the log.
        types = [e.type for e in ctx.events]
        assert types.index(ExecutionEventType.TASK_CANCELLED) < types.index(
            ExecutionEventType.TASK_ROLLBACK_STARTED,
        )

    @pytest.mark.asyncio
    async def test_fallback_is_skipped_on_cancellation(self):
        fallback_ran = False
        rolled_back = False

        async def substitute():
            nonlocal fallback_ran
            fallback_ran = True
            return "substitute"

        async def compensate():
            nonlocal rolled_back
            rolled_back = True

        @task.with_options(fallback=substitute, rollback=compensate)
        async def hang():
            await asyncio.sleep(30)

        @workflow
        async def wf(ctx: ExecutionContext):
            await hang()

        ctx = _make_ctx("wf_cancel_no_fallback")
        run = asyncio.create_task(wf(ctx))
        await asyncio.sleep(0.2)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        # Fallback would fabricate a TASK_COMPLETED that replay honors
        # forever; on cancel only the rollback may run.
        assert not fallback_ran
        assert rolled_back
        assert not _events(ctx, ExecutionEventType.TASK_FALLBACK_STARTED)
        assert not _events(ctx, ExecutionEventType.TASK_COMPLETED)

    @pytest.mark.asyncio
    async def test_cancel_after_terminal_event_is_a_noop(self):
        """A cancel landing between the terminal append and the checkpoint
        must not add TASK_CANCELLED or run the rollback."""
        checkpoint_blocking = asyncio.Event()
        release_checkpoint = asyncio.Event()
        rolled_back = False

        async def compensate():
            nonlocal rolled_back
            rolled_back = True

        @task.with_options(rollback=compensate)
        async def quick():
            return "done"

        @workflow
        async def wf(ctx: ExecutionContext):
            await quick()

        ctx = _make_ctx("wf_cancel_post_terminal")

        async def blocking_checkpoint(context):
            if any(e.type == ExecutionEventType.TASK_COMPLETED for e in context.events):
                checkpoint_blocking.set()
                await release_checkpoint.wait()
            return context

        ctx.set_checkpoint(blocking_checkpoint)

        run = asyncio.create_task(wf(ctx))
        await asyncio.wait_for(checkpoint_blocking.wait(), timeout=5)
        run.cancel()
        release_checkpoint.set()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert not _events(ctx, ExecutionEventType.TASK_CANCELLED)
        assert not rolled_back
        assert len(_events(ctx, ExecutionEventType.TASK_COMPLETED)) == 1


class TestCancelledRollbackDiscipline:
    @pytest.mark.asyncio
    async def test_rollback_survives_a_second_cancel(self):
        """The shield: a re-cancel (worker drain escalation) interrupts the
        wait, not the compensation."""
        rollback_started = asyncio.Event()
        rollback_finished = asyncio.Event()

        async def compensate():
            rollback_started.set()
            await asyncio.sleep(0.3)
            rollback_finished.set()

        @task.with_options(rollback=compensate)
        async def hang():
            await asyncio.sleep(30)

        @workflow
        async def wf(ctx: ExecutionContext):
            await hang()

        ctx = _make_ctx("wf_cancel_twice")
        run = asyncio.create_task(wf(ctx))
        await asyncio.sleep(0.2)
        run.cancel()
        await asyncio.wait_for(rollback_started.wait(), timeout=5)
        run.cancel()  # second signal, mid-rollback
        with pytest.raises(asyncio.CancelledError):
            await run

        assert rollback_finished.is_set()
        assert len(_events(ctx, ExecutionEventType.TASK_ROLLBACK_COMPLETED)) == 1

    @pytest.mark.asyncio
    async def test_wedged_rollback_times_out_and_is_recorded(self, monkeypatch):
        # "flux.task" the attribute resolves to the task *class* (see the
        # _FluxModule note in CLAUDE.md); patch the real submodule.
        monkeypatch.setattr(sys.modules["flux.task"], "CANCELLED_ROLLBACK_TIMEOUT", 0.2)

        async def wedged():
            await asyncio.sleep(30)

        @task.with_options(rollback=wedged)
        async def hang():
            await asyncio.sleep(30)

        @workflow
        async def wf(ctx: ExecutionContext):
            await hang()

        ctx = _make_ctx("wf_cancel_rollback_timeout")
        run = asyncio.create_task(wf(ctx))
        await asyncio.sleep(0.2)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert len(_events(ctx, ExecutionEventType.TASK_ROLLBACK_STARTED)) == 1
        failed = _events(ctx, ExecutionEventType.TASK_ROLLBACK_FAILED)
        assert len(failed) == 1
        assert "did not finish" in str(failed[0].value)
        assert not _events(ctx, ExecutionEventType.TASK_ROLLBACK_COMPLETED)

    @pytest.mark.asyncio
    async def test_uncooperative_rollback_is_abandoned_within_grace(self, monkeypatch):
        """A rollback that suppresses CancelledError cannot be force-killed;
        the wait for it must still be bounded (deadline + drain grace) so it
        cannot strand a drain, and the abandonment must be recorded."""
        mod = sys.modules["flux.task"]
        monkeypatch.setattr(mod, "CANCELLED_ROLLBACK_TIMEOUT", 0.2)
        monkeypatch.setattr(mod, "DEFAULT_DRAIN_TIMEOUT", 0.2)
        stop = asyncio.Event()

        async def stubborn():
            while not stop.is_set():
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    pass  # deliberately ignores cancellation

        @task.with_options(rollback=stubborn)
        async def hang():
            await asyncio.sleep(30)

        @workflow
        async def wf(ctx: ExecutionContext):
            await hang()

        ctx = _make_ctx("wf_cancel_stubborn_rollback")
        run = asyncio.create_task(wf(ctx))
        await asyncio.sleep(0.2)
        loop = asyncio.get_running_loop()
        started = loop.time()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        elapsed = loop.time() - started

        # Deadline (0.2) + grace (0.2) + slack — nowhere near the 30s the
        # stubborn rollback would have imposed on an unbounded wait.
        assert elapsed < 2.0
        failed = _events(ctx, ExecutionEventType.TASK_ROLLBACK_FAILED)
        assert len(failed) == 1
        assert "ignored cancellation" in str(failed[0].value)

        # Let the abandoned rollback exit so the loop closes cleanly.
        stop.set()
        await asyncio.sleep(0.1)


class TestReplayAfterCancellation:
    @pytest.mark.asyncio
    async def test_cancelled_straggler_reruns_on_resume(self):
        """The main replay path: a batch sibling pauses, the straggler is
        cancelled past the drain window, and the resumed execution re-runs it
        — TASK_CANCELLED is audit-only and never short-circuits replay."""
        runs = 0
        rolled_back = asyncio.Event()

        async def compensate():
            rolled_back.set()

        @task.with_options(rollback=compensate)
        async def straggler():
            nonlocal runs
            runs += 1
            if runs == 1:
                await asyncio.sleep(30)  # cancelled past the drain window
            return "done"

        @workflow
        async def wf(ctx: ExecutionContext):
            results = await gather_batch(
                [pause("gate"), straggler()],
                drain_timeout=0.1,
            )
            return results

        ctx = _make_ctx("wf_cancel_replay")
        ctx = await wf(ctx)
        assert ctx.is_paused
        assert runs == 1
        assert rolled_back.is_set()
        assert len(_events(ctx, ExecutionEventType.TASK_CANCELLED)) == 1

        ctx = await wf(ctx)
        assert ctx.has_finished and ctx.has_succeeded
        # The cancelled first run did not short-circuit the resumed one.
        assert runs == 2
        completed = [
            e for e in _events(ctx, ExecutionEventType.TASK_COMPLETED) if e.name == "straggler"
        ]
        assert len(completed) == 1
