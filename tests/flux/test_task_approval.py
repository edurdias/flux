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
