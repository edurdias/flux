"""Timed and completion-triggered pause wakes (issue #145).

``pause(until=/after=)`` and ``pause(on_complete=)`` record a wake
condition inside the WORKFLOW_PAUSED event; the persistence layer stamps
it onto the execution row in the same transaction as the PAUSED state
write (no #70-style window), and the scheduler tick's wake pass resumes
due executions through the ordinary ``start_resuming`` transition.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flux import ExecutionContext
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEventType, ExecutionState
from flux.errors import ExecutionError
from flux.tasks import pause
from flux.workflow import workflow


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'wakes.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield ContextManager.create()
    DatabaseRepository._engines.clear()


def _paused_ctx(execution_id="exec-w1", wake_at=None, wake_on_complete=None):
    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="waiting_wf",
        input=None,
        execution_id=execution_id,
    )
    ctx.pause(
        "wf",
        "gate",
        wake_at=wake_at.isoformat() if wake_at else None,
        wake_on_complete=wake_on_complete,
    )
    return ctx


def _terminal_ctx(execution_id, state=ExecutionState.COMPLETED):
    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="child_wf",
        input=None,
        execution_id=execution_id,
    )
    if state == ExecutionState.COMPLETED:
        ctx.complete("wf", "done")
    elif state == ExecutionState.FAILED:
        ctx.fail("wf", {"type": "X", "message": "boom"})
    return ctx


def _row(manager, execution_id):
    from flux.models import ExecutionContextModel

    with manager.session() as session:
        return session.get(ExecutionContextModel, execution_id)


class TestPauseTaskWakeArguments:
    @pytest.mark.asyncio
    async def test_until_records_wake_in_paused_event(self):
        wake = datetime.now(timezone.utc) + timedelta(hours=1)

        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("await_vendor", until=wake)

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_until",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        ctx = await wf(ctx)

        assert ctx.is_paused
        paused = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        assert paused[0].value["wake_at"] == wake.isoformat()

    @pytest.mark.asyncio
    async def test_until_with_non_utc_offset_is_normalized_to_utc(self):
        from datetime import timezone as tz

        offset = tz(timedelta(hours=-7))
        wake = datetime.now(tz=offset) + timedelta(hours=1)

        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("await_vendor", until=wake)

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_until_tz",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        ctx = await wf(ctx)

        paused = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        stored = datetime.fromisoformat(paused[0].value["wake_at"])
        assert stored.utcoffset() == timedelta(0), "stored instant must carry a UTC offset"
        assert stored == wake, "normalization must not change the instant"

    @pytest.mark.asyncio
    async def test_after_resolves_to_absolute_instant(self):
        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("await_window", after=timedelta(minutes=30))

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_after",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        before = datetime.now(timezone.utc)
        ctx = await wf(ctx)

        paused = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        wake_at = datetime.fromisoformat(paused[0].value["wake_at"])
        assert before + timedelta(minutes=29) < wake_at < before + timedelta(minutes=31)

    @pytest.mark.asyncio
    async def test_on_complete_records_watched_execution(self):
        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("await_child", on_complete="child-exec-42")

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_child",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        ctx = await wf(ctx)

        paused = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        assert paused[0].value["wake_on_complete"] == "child-exec-42"

    @pytest.mark.asyncio
    async def test_multiple_conditions_rejected(self):
        @workflow
        async def wf(ctx: ExecutionContext):
            await pause(
                "bad",
                until=datetime.now(timezone.utc),
                on_complete="other",
            )

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_bad",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        ctx = await wf(ctx)
        assert ctx.has_failed
        assert "at most one" in str(ctx.output)

    @pytest.mark.asyncio
    async def test_plain_pause_event_shape_unchanged(self):
        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("plain")

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_plain",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        ctx = await wf(ctx)
        paused = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        assert "wake_at" not in paused[0].value
        assert "wake_on_complete" not in paused[0].value


class TestWakeColumnStamping:
    def test_paused_save_stamps_wake_at_atomically(self, manager):
        wake = datetime.now(timezone.utc) + timedelta(hours=2)
        manager.save(_paused_ctx(wake_at=wake))
        row = _row(manager, "exec-w1")
        assert row.state == ExecutionState.PAUSED
        assert row.wake_at is not None

    def test_paused_save_stamps_completion_wake(self, manager):
        manager.save(_paused_ctx(wake_on_complete="child-1"))
        assert _row(manager, "exec-w1").wake_on_complete == "child-1"

    def test_resume_clears_wake_columns(self, manager):
        wake = datetime.now(timezone.utc) + timedelta(hours=2)
        ctx = _paused_ctx(wake_at=wake)
        manager.save(ctx)
        ctx.start_resuming()
        manager.save(ctx)
        row = _row(manager, "exec-w1")
        assert row.wake_at is None
        assert row.wake_on_complete is None

    def test_cancel_clears_wake_columns(self, manager):
        wake = datetime.now(timezone.utc) - timedelta(seconds=1)  # already due
        ctx = _paused_ctx(wake_at=wake)
        manager.save(ctx)
        ctx.start_cancel()
        manager.save(ctx)
        row = _row(manager, "exec-w1")
        assert row.wake_at is None
        # And the wake pass cannot resurrect it.
        assert manager.fire_due_wakes() == []

    def test_replayed_pause_checkpoint_is_idempotent(self, manager):
        """Saving the same paused context twice stamps the same wake — no
        duplicate wake records exist because the row *is* the record."""
        wake = datetime.now(timezone.utc) + timedelta(hours=2)
        ctx = _paused_ctx(wake_at=wake)
        manager.save(ctx)
        first = _row(manager, "exec-w1").wake_at
        manager.save(ctx)
        assert _row(manager, "exec-w1").wake_at == first


class TestFireDueWakes:
    def test_due_timed_wake_resumes(self, manager):
        wake = datetime.now(timezone.utc) - timedelta(seconds=5)
        manager.save(_paused_ctx(wake_at=wake))

        assert manager.fire_due_wakes() == ["exec-w1"]
        stored = manager.get("exec-w1")
        assert stored.state == ExecutionState.RESUMING
        assert _row(manager, "exec-w1").wake_at is None

    def test_future_wake_does_not_fire(self, manager):
        wake = datetime.now(timezone.utc) + timedelta(hours=1)
        manager.save(_paused_ctx(wake_at=wake))
        assert manager.fire_due_wakes() == []
        assert _row(manager, "exec-w1").state == ExecutionState.PAUSED

    def test_indefinite_pause_never_fires(self, manager):
        manager.save(_paused_ctx())
        assert manager.fire_due_wakes() == []
        assert _row(manager, "exec-w1").state == ExecutionState.PAUSED

    @pytest.mark.parametrize(
        "child_state",
        [ExecutionState.COMPLETED, ExecutionState.FAILED],
    )
    def test_completion_wake_fires_on_terminal_child(self, manager, child_state):
        manager.save(_terminal_ctx("child-1", state=child_state))
        manager.save(_paused_ctx(wake_on_complete="child-1"))

        assert manager.fire_due_wakes() == ["exec-w1"]
        assert manager.get("exec-w1").state == ExecutionState.RESUMING

    def test_completion_wake_waits_for_running_child(self, manager):
        child = ExecutionContext(
            workflow_id="wf-1",
            workflow_namespace="default",
            workflow_name="child_wf",
            input=None,
            execution_id="child-1",
        )
        manager.save(child)  # CREATED — not terminal
        manager.save(_paused_ctx(wake_on_complete="child-1"))

        assert manager.fire_due_wakes() == []
        assert _row(manager, "exec-w1").state == ExecutionState.PAUSED

    def test_completion_wake_on_missing_child_fires(self, manager):
        """Waiting forever on a typo'd execution id is worse than waking."""
        manager.save(_paused_ctx(wake_on_complete="no-such-execution"))
        assert manager.fire_due_wakes() == ["exec-w1"]

    def test_fired_wake_does_not_refire(self, manager):
        wake = datetime.now(timezone.utc) - timedelta(seconds=5)
        manager.save(_paused_ctx(wake_at=wake))
        assert manager.fire_due_wakes() == ["exec-w1"]
        assert manager.fire_due_wakes() == []

    def test_overdue_backlog_each_fires_once(self, manager):
        """Bounded catch-up after downtime: every overdue wake fires exactly
        once in one pass."""
        wake = datetime.now(timezone.utc) - timedelta(hours=6)
        for i in range(3):
            manager.save(_paused_ctx(execution_id=f"exec-b{i}", wake_at=wake))
        assert sorted(manager.fire_due_wakes()) == ["exec-b0", "exec-b1", "exec-b2"]
        assert manager.fire_due_wakes() == []


class TestPauseValidation:
    @pytest.mark.asyncio
    async def test_non_positive_after_rejected(self):
        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("bad", after=timedelta(seconds=0))

        ctx = ExecutionContext(
            workflow_id="w",
            workflow_namespace="default",
            workflow_name="wf_bad_after",
            input=None,
        )

        async def cp(c):
            return c

        ctx.set_checkpoint(cp)
        ctx = await wf(ctx)
        assert ctx.has_failed


def test_pause_wake_error_is_execution_error():
    assert issubclass(ExecutionError, Exception)
