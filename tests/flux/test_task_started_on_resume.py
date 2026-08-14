"""TASK_STARTED for tasks that begin in a resumed run (issue #244).

A resumed run replays the workflow body: tasks with a terminal event
short-circuit, so anything that reaches the emission site is genuinely
running now. Suppressing its ``TASK_STARTED`` because the *execution* had
resumed left the log with completions that have no start — no duration, and
nothing at all for a task that is still in flight.

The suppression did protect one real case, which these tests pin too: the
task that was mid-flight when the pause happened (``pause`` itself) has a
``TASK_STARTED`` and no terminal event, so replaying the body must not
append a second one for the same call.
"""

from __future__ import annotations

import pytest

from flux import ExecutionContext, task as task_decorator, workflow
from flux.domain.events import ExecutionEventType


def _events(ctx, event_type, name_part):
    return [e for e in ctx.events if e.type == event_type and name_part in e.name]


class TestTaskStartedAcrossResume:
    def test_task_first_run_after_resume_records_its_start(self, isolated_db):
        from flux.tasks import pause

        @task_decorator
        async def before() -> str:
            return "before"

        @task_decorator
        async def after() -> str:
            return "after"

        @workflow
        async def wf(ctx: ExecutionContext):
            first = await before()
            await pause("gate")
            second = await after()
            return [first, second]

        ctx = wf.run()
        assert ctx.is_paused

        resumed = wf.run(execution_id=ctx.execution_id)
        assert resumed.has_succeeded, resumed.output

        started = _events(resumed, ExecutionEventType.TASK_STARTED, "after")
        completed = _events(resumed, ExecutionEventType.TASK_COMPLETED, "after")
        assert len(completed) == 1
        assert len(started) == 1, "a task running for the first time must record its start"
        # Same call, so the pair is joinable — this is what durations and
        # in-flight views are rebuilt from.
        assert started[0].source_id == completed[0].source_id

    def test_replayed_task_records_nothing_new(self, isolated_db):
        """The pre-pause task short-circuits on its stored output: no second
        start, no second completion."""
        from flux.tasks import pause

        runs = [0]

        @task_decorator
        async def before() -> str:
            runs[0] += 1
            return "before"

        @workflow
        async def wf(ctx: ExecutionContext):
            value = await before()
            await pause("gate")
            return value

        ctx = wf.run()
        resumed = wf.run(execution_id=ctx.execution_id)
        assert resumed.has_succeeded

        assert runs == [1], "replay must not re-execute a completed task"
        assert len(_events(resumed, ExecutionEventType.TASK_STARTED, "before")) == 1
        assert len(_events(resumed, ExecutionEventType.TASK_COMPLETED, "before")) == 1

    def test_in_flight_task_at_pause_does_not_duplicate_its_start(self, isolated_db):
        """``pause`` is itself a task: it has a start and no terminal event
        when the execution parks, and the body re-runs it on resume. That is
        the case the blanket suppression existed for."""
        from flux.tasks import pause

        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("gate")
            return "done"

        ctx = wf.run()
        assert ctx.is_paused
        # The gate's name is the task's *argument*; the task itself is `pause`.
        assert len(_events(ctx, ExecutionEventType.TASK_STARTED, "pause")) == 1

        resumed = wf.run(execution_id=ctx.execution_id)
        assert resumed.has_succeeded

        assert len(_events(resumed, ExecutionEventType.TASK_STARTED, "pause")) == 1

    def test_every_turn_of_a_multi_pause_workflow_records_its_own_starts(self, isolated_db):
        """The agent-session shape: pause, work, pause, work. Each turn's
        tasks are new, so each turn's starts must land."""
        from flux.tasks import pause

        @task_decorator
        async def turn_one() -> str:
            return "one"

        @task_decorator
        async def turn_two() -> str:
            return "two"

        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("turn-1")
            first = await turn_one()
            await pause("turn-2")
            second = await turn_two()
            return [first, second]

        ctx = wf.run()
        ctx = wf.run(execution_id=ctx.execution_id)
        assert ctx.is_paused, "second gate should park the execution again"
        final = wf.run(execution_id=ctx.execution_id)
        assert final.has_succeeded, final.output

        for name in ("turn_one", "turn_two"):
            assert len(_events(final, ExecutionEventType.TASK_STARTED, name)) == 1, name
            assert len(_events(final, ExecutionEventType.TASK_COMPLETED, name)) == 1, name

    @pytest.mark.parametrize("occurrences", [2, 3])
    def test_repeated_calls_after_resume_each_record_their_own_start(
        self,
        isolated_db,
        occurrences,
    ):
        """Repeated identical calls get distinct ids (``~1``, ``~2``), so each
        one is a separate call with its own start."""
        from flux.tasks import pause

        @task_decorator
        async def echo(value: str) -> str:
            return value

        @workflow
        async def wf(ctx: ExecutionContext):
            await pause("gate")
            for _ in range(occurrences):
                await echo("same")
            return "ok"

        ctx = wf.run()
        resumed = wf.run(execution_id=ctx.execution_id)
        assert resumed.has_succeeded

        started = _events(resumed, ExecutionEventType.TASK_STARTED, "echo")
        completed = _events(resumed, ExecutionEventType.TASK_COMPLETED, "echo")
        assert len(started) == occurrences
        assert len(completed) == occurrences
        assert {e.source_id for e in started} == {e.source_id for e in completed}
