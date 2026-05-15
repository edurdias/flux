import inspect

import pytest

from flux import task


def test_task_init_accepts_requires_approval_kwarg():
    sig = inspect.signature(task.__init__)
    assert "requires_approval" in sig.parameters
    assert sig.parameters["requires_approval"].default is False


def test_with_options_decorator_sets_requires_approval_true():
    @task.with_options(requires_approval=True)
    async def t():
        return 1

    assert t.requires_approval is True


def test_with_options_decorator_sets_requires_approval_callable():
    pred = lambda amount: amount > 100  # noqa: E731

    @task.with_options(requires_approval=pred)
    async def t(amount: int):
        return amount

    assert t.requires_approval is pred


def test_with_options_method_preserves_existing_value_when_none():
    @task
    async def t():
        return 1

    base = t.with_options(requires_approval=True)
    derived = base.with_options()
    assert derived.requires_approval is True


def test_with_options_method_can_explicitly_set_false():
    @task
    async def t():
        return 1

    base = t.with_options(requires_approval=True)
    derived = base.with_options(requires_approval=False)
    assert derived.requires_approval is False


def test_default_requires_approval_is_false():
    @task
    async def t():
        return 1

    assert t.requires_approval is False


def _build_test_ctx():
    """Construct a minimal ExecutionContext for tests."""
    from flux.domain.execution_context import ExecutionContext

    return ExecutionContext(
        workflow_id="test_wf",
        workflow_namespace="default",
        workflow_name="test",
        input=None,
    )


def test_execution_context_has_approval_bypass_default_false():
    ctx = _build_test_ctx()
    assert ctx.approval_bypass is False


def test_execution_context_approval_bypass_can_be_set():
    ctx = _build_test_ctx()
    ctx.approval_bypass = True
    assert ctx.approval_bypass is True


def test_await_approval_pending_raises_pause_requested(isolated_db):
    """If the approval row doesn't exist or is pending, _await_approval raises PauseRequested."""
    from flux.tasks.pause import PauseRequested

    ctx = _build_test_ctx()
    with pytest.raises(PauseRequested):
        ctx._await_approval("nonexistent-call-id")


def test_await_approval_pending_pause_carries_metadata_payload(isolated_db):
    """When a real PENDING row exists, the PauseRequested output carries the
    approval metadata so SSE consumers (the agent harness) can render a
    human-meaningful prompt from the workflow_paused event."""
    from flux.approvals import ApprovalManager
    from flux.tasks.pause import PauseRequested
    from flux.unit_of_work import UnitOfWork

    ctx = _build_test_ctx()
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        mgr.create(ctx.execution_id, "call-meta-1", "billing", "release", "deploy", uow=uow)
        uow.commit()

    with pytest.raises(PauseRequested) as exc_info:
        ctx._await_approval("call-meta-1")

    output = exc_info.value.output
    assert output is not None
    assert output["type"] == "approval_required"
    assert output["task_call_id"] == "call-meta-1"
    assert output["task_name"] == "deploy"
    assert output["workflow_namespace"] == "billing"
    assert output["workflow_name"] == "release"
    assert output["execution_id"] == ctx.execution_id
    assert output["approval_id"]  # opaque id is present
    assert output["requested_at"]  # ISO timestamp present


def test_await_approval_approved_returns_verdict(isolated_db):
    from flux.approvals import ApprovalManager
    from flux.unit_of_work import UnitOfWork

    ctx = _build_test_ctx()
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        mgr.create(ctx.execution_id, "call-app-1", "default", "test", "step", uow=uow)
        uow.commit()
    with UnitOfWork() as uow:
        mgr.decide(
            ctx.execution_id,
            "call-app-1",
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason="ok",
            uow=uow,
        )
        uow.commit()
    verdict = ctx._await_approval("call-app-1")
    assert verdict.approved is True
    assert verdict.approver_subject == "alice"
    assert verdict.cancelled is False


def test_await_approval_rejected_raises_approval_rejected(isolated_db):
    from flux.approvals import ApprovalManager, ApprovalRejected
    from flux.unit_of_work import UnitOfWork

    ctx = _build_test_ctx()
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        mgr.create(ctx.execution_id, "call-rej-1", "default", "test", "step", uow=uow)
        uow.commit()
    with UnitOfWork() as uow:
        mgr.decide(
            ctx.execution_id,
            "call-rej-1",
            approver_subject="bob",
            approver_provider="oidc",
            approved=False,
            reason="no",
            uow=uow,
        )
        uow.commit()
    with pytest.raises(ApprovalRejected) as exc_info:
        ctx._await_approval("call-rej-1")
    assert exc_info.value.approver_subject == "bob"


def test_await_approval_cancelled_returns_cancelled_verdict(isolated_db):
    from flux.approvals import ApprovalManager
    from flux.unit_of_work import UnitOfWork

    ctx = _build_test_ctx()
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        mgr.create(ctx.execution_id, "call-cnl-1", "default", "test", "step", uow=uow)
        uow.commit()
    with UnitOfWork() as uow:
        count = mgr.cancel_pending_for_execution(ctx.execution_id, uow=uow)
        uow.commit()
    assert count == 1
    verdict = ctx._await_approval("call-cnl-1")
    assert verdict.cancelled is True
    assert verdict.approved is False


# ---------------------------------------------------------------------------
# Engine integration tests (Task 10): predicate + pause-for-approval gate
# ---------------------------------------------------------------------------


from flux import ExecutionContext, task as task_decorator, workflow  # noqa: E402
from flux.approvals import ApprovalManager  # noqa: E402
from flux.models import ApprovalStatus  # noqa: E402
from flux.unit_of_work import UnitOfWork  # noqa: E402


def test_static_true_pauses_workflow(isolated_db):
    """A task with requires_approval=True should pause the workflow at first call."""

    @task_decorator.with_options(requires_approval=True)
    async def gated() -> str:
        return "ran"

    @workflow
    async def wf_static_true(ctx: ExecutionContext):
        return await gated()

    ctx = wf_static_true.run()
    assert ctx.is_paused, f"Expected workflow to pause, state={ctx.state}"
    mgr = ApprovalManager()
    rows = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    assert len(rows) == 1


def test_static_false_runs_body_normally(isolated_db):
    @task_decorator
    async def normal() -> str:
        return "ok"

    @workflow
    async def wf_static_false(ctx: ExecutionContext):
        return await normal()

    ctx = wf_static_false.run()
    assert ctx.has_succeeded
    assert ctx.output == "ok"
    mgr = ApprovalManager()
    rows = mgr.list(execution_id=ctx.execution_id)
    assert len(rows) == 0


def test_callable_predicate_true_pauses(isolated_db):
    @task_decorator.with_options(requires_approval=lambda amount: amount > 100)
    async def conditional_true(amount: int) -> str:
        return f"ran with {amount}"

    @workflow
    async def wf_pred_true(ctx: ExecutionContext[int]):
        return await conditional_true(ctx.input)

    ctx = wf_pred_true.run(150)
    assert ctx.is_paused
    mgr = ApprovalManager()
    rows = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    assert len(rows) == 1


def test_callable_predicate_false_runs_body(isolated_db):
    @task_decorator.with_options(requires_approval=lambda amount: amount > 100)
    async def conditional_false(amount: int) -> str:
        return f"ran with {amount}"

    @workflow
    async def wf_pred_false(ctx: ExecutionContext[int]):
        return await conditional_false(ctx.input)

    ctx = wf_pred_false.run(50)
    assert ctx.has_succeeded
    assert ctx.output == "ran with 50"
    mgr = ApprovalManager()
    rows = mgr.list(execution_id=ctx.execution_id)
    assert len(rows) == 0


def test_approval_bypass_skips_gate(isolated_db):
    @task_decorator.with_options(requires_approval=True)
    async def gated_bypass() -> str:
        return "bypassed"

    @workflow
    async def wf_bypass(ctx: ExecutionContext):
        ctx.approval_bypass = True
        return await gated_bypass()

    ctx = wf_bypass.run()
    assert ctx.has_succeeded
    assert ctx.output == "bypassed"
    mgr = ApprovalManager()
    assert mgr.list(execution_id=ctx.execution_id) == []


def test_approval_after_resume_runs_body(isolated_db):
    @task_decorator.with_options(requires_approval=True)
    async def gated_resume() -> str:
        return "ran"

    @workflow
    async def wf_resume(ctx: ExecutionContext):
        return await gated_resume()

    ctx = wf_resume.run()
    assert ctx.is_paused
    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    row = pending[0]
    with UnitOfWork() as uow:
        mgr.decide(
            row.execution_id,
            row.task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason="ok",
            uow=uow,
        )
        uow.commit()
    ctx = wf_resume.run(execution_id=ctx.execution_id)
    assert ctx.has_succeeded
    assert ctx.output == "ran"


def test_rejection_raises_approval_rejected(isolated_db):
    @task_decorator.with_options(requires_approval=True)
    async def gated_reject() -> str:
        return "ran"

    @workflow
    async def wf_reject(ctx: ExecutionContext):
        return await gated_reject()

    ctx = wf_reject.run()
    assert ctx.is_paused
    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    row = pending[0]
    with UnitOfWork() as uow:
        mgr.decide(
            row.execution_id,
            row.task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=False,
            reason="not safe",
            uow=uow,
        )
        uow.commit()
    ctx = wf_reject.run(execution_id=ctx.execution_id)
    assert ctx.has_failed
    last_value = str(ctx.events[-1].value)
    assert "Approval rejected" in last_value or "Approval rejected" in str(ctx.exception)


def test_rejected_does_not_trigger_retry(isolated_db):

    retry_count = [0]

    @task_decorator.with_options(requires_approval=True, retry_max_attempts=3)
    async def gated_with_retry() -> str:
        retry_count[0] += 1
        return "should not run"

    @workflow
    async def wf_retry(ctx: ExecutionContext):
        return await gated_with_retry()

    ctx = wf_retry.run()
    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    with UnitOfWork() as uow:
        mgr.decide(
            pending[0].execution_id,
            pending[0].task_call_id,
            approver_subject="a",
            approver_provider="oidc",
            approved=False,
            reason=None,
            uow=uow,
        )
        uow.commit()
    ctx = wf_retry.run(execution_id=ctx.execution_id)
    assert ctx.has_failed
    assert retry_count[0] == 0, f"Body ran {retry_count[0]} times; should never run on rejection"


def test_rejected_does_not_trigger_fallback(isolated_db):

    fallback_called = [False]

    async def my_fallback(*args, **kwargs):
        fallback_called[0] = True
        return "fallback ran"

    @task_decorator.with_options(requires_approval=True, fallback=my_fallback)
    async def gated_with_fallback() -> str:
        return "primary"

    @workflow
    async def wf_fallback(ctx: ExecutionContext):
        return await gated_with_fallback()

    ctx = wf_fallback.run()
    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    with UnitOfWork() as uow:
        mgr.decide(
            pending[0].execution_id,
            pending[0].task_call_id,
            approver_subject="a",
            approver_provider="oidc",
            approved=False,
            reason=None,
            uow=uow,
        )
        uow.commit()
    ctx = wf_fallback.run(execution_id=ctx.execution_id)
    assert ctx.has_failed
    assert fallback_called[0] is False


# ---------------------------------------------------------------------------
# Determinism: predicate must not re-run when an approval row already exists
# ---------------------------------------------------------------------------


def test_predicate_not_reevaluated_when_row_exists(isolated_db):
    """Replay-on-reclaim must not re-run a non-deterministic predicate.

    Regression for: predicate flips True -> False on replay would silently
    bypass the gate and run the task body without approval. The fix is to
    treat the existing approval row as the durable record that the gate
    was triggered, and only consult the predicate when no row exists.
    """
    from flux.approvals import ApprovalManager
    from flux.models import ApprovalStatus
    from flux.unit_of_work import UnitOfWork

    call_count = [0]

    def flipping_predicate(*_args, **_kwargs):
        call_count[0] += 1
        # First call returns True (gate triggered), every subsequent call
        # returns False — simulates a non-deterministic predicate that
        # would silently bypass the gate on replay if consulted again.
        return call_count[0] == 1

    @task_decorator.with_options(requires_approval=flipping_predicate)
    async def gated_flipping() -> str:
        return "should-only-run-after-approval"

    @workflow
    async def wf_flip(ctx: ExecutionContext):
        return await gated_flipping()

    ctx = wf_flip.run()
    assert ctx.is_paused, f"Expected pause on first call, got state={ctx.state}"
    assert call_count[0] == 1, "Predicate should run exactly once on first call"

    # Approve so the workflow can resume to completion.
    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    with UnitOfWork() as uow:
        mgr.decide(
            pending[0].execution_id,
            pending[0].task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason=None,
            uow=uow,
        )
        uow.commit()

    ctx = wf_flip.run(execution_id=ctx.execution_id)
    assert ctx.has_succeeded
    assert ctx.output == "should-only-run-after-approval"
    # Resume re-enters the gate; the row exists so the predicate must NOT
    # be consulted again, otherwise the flipping predicate would falsely
    # report "no approval required" and silently bypass the gate.
    assert call_count[0] == 1, (
        f"Predicate ran {call_count[0]} times; should run exactly once "
        f"because the approval row is the durable record on replay."
    )


# ---------------------------------------------------------------------------
# Approval rejection emits TASK_FAILED via output_storage
# ---------------------------------------------------------------------------


def test_rejection_emits_task_failed_event_via_output_storage(isolated_db):
    """ApprovalRejected must be recorded as TASK_FAILED in the event log,
    routed through the task's output_storage so replay re-raises identically.
    """
    from flux.approvals import ApprovalManager, ApprovalRejected
    from flux.domain.events import ExecutionEventType
    from flux.models import ApprovalStatus
    from flux.output_storage import OutputStorageReference
    from flux.unit_of_work import UnitOfWork

    @task_decorator.with_options(requires_approval=True)
    async def gated_rej() -> str:
        return "never"

    @workflow
    async def wf_rej(ctx: ExecutionContext):
        return await gated_rej()

    ctx = wf_rej.run()
    assert ctx.is_paused

    mgr = ApprovalManager()
    pending = mgr.list(execution_id=ctx.execution_id, status=ApprovalStatus.PENDING)
    with UnitOfWork() as uow:
        mgr.decide(
            pending[0].execution_id,
            pending[0].task_call_id,
            approver_subject="alice",
            approver_provider="oidc",
            approved=False,
            reason="not safe",
            uow=uow,
        )
        uow.commit()

    ctx = wf_rej.run(execution_id=ctx.execution_id)
    assert ctx.has_failed

    failed = [e for e in ctx.events if e.type == ExecutionEventType.TASK_FAILED]
    assert len(failed) == 1, "Expected a TASK_FAILED event for the rejected approval"
    # Persisted via output_storage — the value is a reference, not the
    # bare exception (so the configured backend handles failure persistence
    # the same way it handles success persistence).
    assert isinstance(failed[0].value, OutputStorageReference)

    # Round-trip back through retrieve to confirm fidelity.
    retrieved = gated_rej.output_storage.retrieve(failed[0].value)
    assert isinstance(retrieved, ApprovalRejected)
    assert retrieved.reason == "not safe"
    assert retrieved.approver_subject == "alice"
